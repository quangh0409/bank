# Step 1: Install required libraries
# pip install pymongo flask pytest requests geopy

import logging
from flask import Flask, request, jsonify
from pymongo import MongoClient, GEOSPHERE
import requests
import subprocess
import json
import os
from bson import ObjectId
from geopy.distance import geodesic
import uuid
import hmac
import hashlib

# ========================== LỚP 1: QUẢN LÝ DANH TÍNH VÀ NGƯỜI DÙNG ==========================
# Setup logging
# Cấu hình hệ thống ghi log để theo dõi các sự kiện và giao dịch, giúp kiểm tra lỗi và giám sát hoạt động của hệ thống.
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Database setup
# Kết nối đến MongoDB để lưu trữ thông tin giao dịch và người dùng với hỗ trợ geospatial indexing
client = MongoClient("mongodb+srv://quangvt5:quangvt5@quangvt.1qzn9ya.mongodb.net/")
db = client["zkp_bank"]
users_collection = db["users"]
transactions_collection = db["transactions"]

# Cấu hình MoMo
MOMO_CONFIG = {
    "endpoint": "https://test-payment.momo.vn/v2/gateway/api/create",
    "accessKey": "F8BBA842ECF85",
    "secretKey": "K951B6PE1waDMi640xX08PD3vg6EkVlz",
    "partnerCode": "MOMO",
    "redirectUrl": "https://webhook.site/b3088a6a-2d17-4f8d-a383-71389a6c600b",
    "ipnUrl": "https://webhook.site/b3088a6a-2d17-4f8d-a383-71389a6c600b",
    "lang": "vi"
}

# Tạo geospatial index cho truy vấn theo vị trí (chạy 1 lần)
try:
    transactions_collection.create_index([("location", GEOSPHERE)])
except Exception as e:
    logging.warning(f"Geospatial index đã tồn tại: {e}")

# ========================== LỚP 2: XÁC MINH GIAO DỊCH VỚI ZKP ==========================
# Save transaction to MongoDB với thông tin vị trí
def save_transaction(user_id, amount, status, reason, lat, lng, payment_info=None):
    """Lưu trữ giao dịch kèm thông tin vị trí địa lý và thanh toán"""
    logging.info("Bắt đầu lưu giao dịch")
    
    try:
        # Kiểm tra kết nối MongoDB
        db.command('ping')
        logging.info("Kết nối MongoDB thành công")
        
        # Tạo timestamp
        from datetime import datetime
        timestamp = datetime.now().isoformat()
        logging.info(f"Timestamp: {timestamp}")
        
        # Tạo đối tượng transaction
        transaction = {
            "user_id": user_id,
            "amount": amount,
            "status": status,
            "reason": reason,
            "location": {
                "type": "Point",
                "coordinates": [lng, lat]  # Chuẩn GeoJSON: [kinh độ, vĩ độ]
            },
            "timestamp": timestamp
        }
        logging.info("Đã tạo transaction object")
        
        # Xử lý payment_info
        if payment_info:
            logging.info(f"Payment info nhận được: {payment_info}")
            if isinstance(payment_info, str):
                try:
                    payment_info = json.loads(payment_info)
                    logging.info("Đã parse payment_info từ JSON string")
                except json.JSONDecodeError as e:
                    logging.error(f"Lỗi decode payment_info: {str(e)}")
                    payment_info = None
            elif not isinstance(payment_info, dict):
                logging.error(f"payment_info không phải dictionary: {type(payment_info)}")
                payment_info = None
            
            if payment_info:
                transaction["payment_info"] = payment_info
                logging.info("Đã thêm payment_info vào transaction")
        
        # Log thông tin giao dịch
        logging.info(f"Giao dịch: {json.dumps(transaction, indent=2, ensure_ascii=False)}")
        
        # Lưu vào database
        logging.info("Bắt đầu lưu vào MongoDB")
        result = transactions_collection.insert_one(transaction)
        logging.info(f"Đã lưu giao dịch với ID: {result.inserted_id}")
        
    except Exception as e:
        logging.error(f"Lỗi khi lưu giao dịch: {str(e)}", exc_info=True)
        raise

# Function to generate and verify ZKP using ZoKrates CLI
def generate_proof(balance, amount):
    """Tạo và xác minh bằng chứng không tiết lộ thông tin (ZKP)"""
    
    # Thực thi lệnh trong WSL với path chính xác
    docker_project_path = "//demo/"
    
    # Thực thi lệnh trong ZoKrates Docker container
        #Mục đích của các lệnh này có thể là để biên dịch một chương trình ZoKrates, thiết lập khóa, tính toán witness, tạo proof và xác minh proof.

    #lệnh đầu tiên: `docker exec zokrates-container zokrates compile -i {docker_project_path}balance_check.zok`. 
    #Lệnh này chạy lệnh `zokrates compile` trong container Docker có tên là `zokrates-container`, biên dịch file `balance_check.zok` từ đường dẫn được chỉ định.
    #
    #Lệnh thứ hai: `docker exec zokrates-container zokrates setup`. Đây là bước thiết lập (setup) để tạo các khóa proving và verifying. 
    #Bước này cần thiết để tạo ra các khóa công khai và riêng tư cho zk-SNARKs.
    #
    #Lệnh thứ ba: `docker exec zokrates-container zokrates compute-witness -a {balance} {amount}`. 
    #Lệnh này tính toán witness, tức là các giá trị đầu vào cụ thể (balance và amount) để chứng minh tính đúng đắn của chương trình. Tham số `-a` dùng để truyền các đối số vào chương trình.
    #
    #Lệnh thứ tư: `docker exec zokrates-container zokrates generate-proof`. 
    #Lệnh này tạo ra proof dựa trên witness đã tính toán và các khóa đã thiết lập. Proof này sẽ được sử dụng để xác minh mà không cần tiết lộ thông tin đầu vào.
    #
    #Lệnh cuối cùng: `docker exec zokrates-container zokrates verify`. 
    #Lệnh này xác minh proof đã tạo, đảm bảo rằng proof là hợp lệ và được tạo từ các khóa và witness chính xác.
    #
    #Mình cần đảm bảo rằng các lệnh này được thực thi theo đúng thứ tự, từ biên dịch, setup, tính witness, tạo proof đến xác minh.
    #Đồng thời, cần kiểm tra xem container `zokrates-container` đã được khởi chạy và các file cần thiết đã được mount đúng vào container chưa. Nếu có lỗi xảy ra, có thể do đường dẫn không chính xác, thiếu file, hoặc container chưa được cấu hình đúng.
   
    commands = [
        f"docker exec zokrates-container zokrates compile -i {docker_project_path}balance_check.zok",
        f"docker exec zokrates-container zokrates setup",
        f"docker exec zokrates-container zokrates compute-witness -a {balance} {amount}",
        f"docker exec zokrates-container zokrates generate-proof",
        f"docker exec zokrates-container zokrates verify"
    ]
    
    try:
        for cmd in commands:
            # Thực thi từng lệnh và kiểm tra kết quả
            result = subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
            logging.info(f"Command: {cmd}")
            logging.info(f"Command output: {result.stdout}")
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"Lỗi ZKP: {e.stderr}")
        return False

# ========================== LỚP 3: TÍCH HỢP VỚI HỆ THỐNG NGÂN HÀNG ==========================
# Flask Backend API
app = Flask(__name__)

# API để tạo user với thông tin vị trí
@app.route('/users', methods=['POST'])
def create_user():
    """Tạo người dùng mới với thông tin số dư và vị trí địa lý"""
    data = request.json
    try:
        user = {
            "name": data["name"],
            "age": data["age"],
            "balance": data["balance"],
            "location": {
                "type": "Point",
                "coordinates": [data["lng"], data["lat"]]
            }
        }
        
        # Validate GPS coordinates
        if not (-90 <= data["lat"] <= 90) or not (-180 <= data["lng"] <= 180):
            return jsonify({"error": "Tọa độ GPS không hợp lệ"}), 400
            
        result = users_collection.insert_one(user)
        user["_id"] = str(result.inserted_id)
        return jsonify({
            "message": "Tạo user thành công",
            "user": {
                "id": str(result.inserted_id),  # Sử dụng 'id' thay vì '_id'
                "name": user["name"],
                "balance": user["balance"]
            }
        }), 201
        
    except KeyError as e:
        return jsonify({"error": f"Thiếu trường: {str(e)}"}), 400
    except Exception as e:
        logging.error(str(e))
        return jsonify({"error": "Lỗi server"}), 500

@app.route('/transaction/verify', methods=['POST'])
def verify_transaction():
    """Xác thực giao dịch với ZKP và tích hợp thanh toán MoMo"""
    data = request.json
    
    # Kiểm tra các trường bắt buộc
    required_fields = ["user_id", "amount", "lat", "lng"]
    if any(field not in data for field in required_fields):
        return jsonify({"error": f"Thiếu trường bắt buộc: {required_fields}"}), 400
    
    try:
        # Validate và chuyển đổi tọa độ
        lat = float(data["lat"])
        lng = float(data["lng"])
        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            raise ValueError("Tọa độ không hợp lệ")
            
        # Tìm thông tin user
        user = users_collection.find_one({"_id": ObjectId(data["user_id"])})
        logging.info(user)
        if not user:
            return jsonify({"error": "Không tìm thấy user"}), 404
        
        # Xác minh bằng ZKP
        if generate_proof(user["balance"], data["amount"]):
            logging.info("Tạo thanh toán MoMo")
            # Tạo thanh toán MoMo
            momo_response = create_momo_payment(
                amount=data["amount"],
                order_info=f"Thanh toán cho user {data['user_id']}"
            )
            
            if momo_response.get("resultCode") != 0:
                return jsonify({
                    "status": "ERROR",
                    "message": "Không thể tạo thanh toán",
                    "momo_error": momo_response
                }), 400
            
            # Cập nhật số dư và lưu giao dịch
            new_balance = user["balance"] - data["amount"]
            users_collection.update_one(
                {"_id": ObjectId(data["user_id"])}, 
                {"$set": {"balance": new_balance}}
            )
            
            save_transaction(
                user_id=data["user_id"],
                amount=data["amount"],
                status="APPROVED",
                reason="Xác minh thành công",
                lat=lat,
                lng=lng,
                payment_info=momo_response
            )
            
            return jsonify({
                "status": "APPROVED",
                "new_balance": new_balance,
                "payment_url": momo_response.get("payUrl"),
                "momo_order_id": momo_response.get("orderId")
            }), 200
        else:
            save_transaction(
                user_id=data["user_id"],
                amount=data["amount"],
                status="DENIED", 
                reason="Xác minh số dư thất bại",
                lat=lat,
                lng=lng
            )
            return jsonify({
                "status": "DENIED",
                "reason": "Không thể xác minh số dư"
            }), 403

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        logging.error(f"Lỗi hệ thống: {str(e)}")
        return jsonify({"error": "Xử lý giao dịch thất bại"}), 500

@app.route('/transactions', methods=['GET'])
def get_transactions():
    """Lấy lịch sử giao dịch với bộ lọc theo vị trí"""
    try:
        # Truy vấn theo phạm vi địa lý
        if all(k in request.args for k in ["lat", "lng", "radius"]):
            lat = float(request.args["lat"])
            lng = float(request.args["lng"])
            radius = float(request.args["radius"])
            
            query = {
                "location": {
                    "$nearSphere": {
                        "$geometry": {
                            "type": "Point",
                            "coordinates": [lng, lat]
                        },
                        "$maxDistance": radius
                    }
                }
            }
        else:
            query = {}
        
        transactions = list(transactions_collection.find(query, {"_id": 0}).limit(100))
        return jsonify({"count": len(transactions), "transactions": transactions}), 200
    
    except ValueError:
        return jsonify({"error": "Tham số địa lý không hợp lệ"}), 400
    except Exception as e:
        logging.error(str(e))
        return jsonify({"error": "Lỗi database"}), 500
    
@app.route('/payment', methods=['POST'])
def create_momo_payment(amount, order_info):
    """Tạo yêu cầu thanh toán qua MoMo"""
    try:
        order_id = str(uuid.uuid4())
        request_id = str(uuid.uuid4())
        
        raw_signature = f"accessKey={MOMO_CONFIG['accessKey']}&amount={amount}&extraData=&ipnUrl={MOMO_CONFIG['ipnUrl']}" \
                        f"&orderId={order_id}&orderInfo={order_info}&partnerCode={MOMO_CONFIG['partnerCode']}" \
                        f"&redirectUrl={MOMO_CONFIG['redirectUrl']}&requestId={request_id}&requestType=payWithMethod"
        
        signature = hmac.new(
            bytes(MOMO_CONFIG['secretKey'], 'utf-8'),
            bytes(raw_signature, 'utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        data = {
            'partnerCode': MOMO_CONFIG['partnerCode'],
            'orderId': order_id,
            'partnerName': "MoMo Payment",
            'storeId': "Test Store",
            'ipnUrl': MOMO_CONFIG['ipnUrl'],
            'amount': amount,
            'lang': MOMO_CONFIG['lang'],
            'requestType': "payWithMethod",
            'redirectUrl': MOMO_CONFIG['redirectUrl'],
            'autoCapture': True,
            'orderInfo': order_info,
            'requestId': request_id,
            'extraData': "",
            'signature': signature,
            'orderGroupId': ""
        }
        
        logging.info(f"MoMo request data: {json.dumps(data, indent=2)}")
        
        response = requests.post(
            MOMO_CONFIG['endpoint'],
            data=json.dumps(data),
            headers={'Content-Type': 'application/json'},
            timeout=30
        )
        
        logging.info(f"MoMo response: {response.status_code} - {response.text}")
        
        return response.json()
    
    except Exception as e:
        logging.error(f"Lỗi khi tạo thanh toán MoMo: {str(e)}", exc_info=True)
        return {"resultCode": -1, "message": str(e)}
# ========================== LỚP 4: LƯU TRỮ DỮ LIỆU VÀ LỊCH SỬ ==========================
def test_transaction_flow():
    """Kiểm tra toàn bộ luồng giao dịch với ZKP và GPS"""
    with app.test_client() as client:
        # Tạo header và URL base
        headers = {"Content-Type": "application/json"}
        base_url = "http://localhost:5000"

        # Test 1: Tạo user thành công
        user_data = {
            "name": "John Doe",
            "age": 30,
            "balance": 1000,
            "lat": 21.0285,
            "lng": 105.8542
        }
        response = client.post('/users', json=user_data, headers=headers)
        assert response.status_code == 201
        user_data = response.json["user"]
        user_id = user_data["id"]  # Truy cập đúng theo cấu trúc mới
        logging.info(f"User created: {user_id}")

        # Test 2: Giao dịch hợp lệ
        valid_transaction = {
            "user_id": user_id,
            "amount": 500,
            "lat": 21.0285,
            "lng": 105.8542
        }
        response = client.post('/transaction/verify', json=valid_transaction, headers=headers)
        assert response.status_code == 200
        assert response.json["status"] == "APPROVED"
        logging.info("Valid transaction passed")

        # Test 3: Giao dịch không hợp lệ (số dư không đủ)
        invalid_transaction = {
            "user_id": user_id,
            "amount": 1500,
            "lat": 21.0285,
            "lng": 105.8542
        }
        response = client.post('/transaction/verify', json=invalid_transaction, headers=headers)
        assert response.status_code == 403
        assert response.json["status"] == "DENIED"
        logging.info("Invalid transaction passed")

        # Test 4: Giao dịch từ vị trí đáng ngờ
        suspicious_transaction = {
            "user_id": user_id,
            "amount": 500,
            "lat": 10.762622,
            "lng": 106.660172
        }
        response = client.post('/transaction/verify', json=suspicious_transaction, headers=headers)
        assert response.status_code == 403
        logging.info("Geofencing test passed")

        print("✅ Tất cả test đều passed!")

if __name__ == '__main__':
    # Chạy test mà không cần server bên ngoài
    if os.environ.get('RUN_TESTS'):
        logging.basicConfig(level=logging.DEBUG)
        with app.app_context():
            test_transaction_flow()
    else:
        app.run(host='0.0.0.0', port=5000, debug=True)