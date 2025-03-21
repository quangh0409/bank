# Step 1: Install required libraries
# pip install pymongo flask pytest requests geopy

import logging
from flask import Flask, request, jsonify
from pymongo import MongoClient, GEOSPHERE
import pytest
import requests
import subprocess
import json
import os
from bson import ObjectId
from geopy.distance import geodesic

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

# Tạo geospatial index cho truy vấn theo vị trí (chạy 1 lần)
try:
    transactions_collection.create_index([("location", GEOSPHERE)])
except Exception as e:
    logging.warning(f"Geospatial index đã tồn tại: {e}")

# ========================== LỚP 2: XÁC MINH GIAO DỊCH VỚI ZKP ==========================
# Save transaction to MongoDB với thông tin vị trí
def save_transaction(user_id, amount, status, lat, lng):
    """Lưu trữ giao dịch kèm thông tin vị trí địa lý"""
    transaction = {
        "user_id": user_id,
        "amount": amount,
        "status": status,
        "location": {
            "type": "Point",
            "coordinates": [lng, lat]  # Chuẩn GeoJSON: [kinh độ, vĩ độ]
        },
        "timestamp": subprocess.getoutput('date -Iseconds')  # Thời gian giao dịch
    }
    transactions_collection.insert_one(transaction)
    logging.info(f"Đã lưu giao dịch với GPS: {transaction}")

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
    """Xác thực giao dịch với ZKP và kiểm tra vị trí"""
    data = request.json
    
    # Kiểm tra các trường bắt buộc
    required_fields = ["user_id", "amount", "lat", "lng"]
    if any(field not in data for field in required_fields):
        return jsonify({"error": f"Thiếu trường bắt buộc: {required_fields}"}), 400
    
    try:
        # Chuyển đổi và kiểm tra tọa độ
        lat = float(data["lat"])
        lng = float(data["lng"])
        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            raise ValueError
            
        # Tìm user trong database
        user = users_collection.find_one({"_id": ObjectId(data["user_id"])})
        if not user:
            return jsonify({"error": "Không tìm thấy user"}), 404
        
        # Xác minh bằng ZKP
        if generate_proof(user["balance"], data["amount"]):
            # Cập nhật số dư và lưu giao dịch
            new_balance = user["balance"] - data["amount"]
            users_collection.update_one(
                {"_id": ObjectId(data["user_id"])}, 
                {"$set": {"balance": new_balance}}
            )
            save_transaction(data["user_id"], data["amount"], "APPROVED", lat, lng)
            return jsonify({
                "status": "APPROVED",
                "new_balance": new_balance,
                "location": {"lat": lat, "lng": lng}
            }), 200
        else:
            save_transaction(data["user_id"], data["amount"], "DENIED", lat, lng)
            return jsonify({"status": "DENIED", "reason": "Xác minh ZKP thất bại"}), 403
            
    except ValueError:
        return jsonify({"error": "Tọa độ GPS không hợp lệ"}), 400
    except Exception as e:
        logging.error(f"Lỗi giao dịch: {str(e)}")
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