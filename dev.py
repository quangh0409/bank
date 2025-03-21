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