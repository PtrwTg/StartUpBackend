from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException
from starlette.responses import FileResponse  # นำเข้า FileResponse จาก starlette
import pandas as pd
import numpy as np
from pydantic import BaseModel
import os
import logging
from typing import List
from fastapi.responses import JSONResponse
import uuid  # ใช้ UUID


# add multi
# ตั้งค่า Logging เพื่อช่วย Debug
logging.basicConfig(level=logging.DEBUG)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
) 

# โหลดไฟล์ CSV และอ่าน
file_path = 'RFT 2024.csv'  
if not os.path.exists(file_path):
    raise FileNotFoundError(f"CSV file not found: {file_path}")

try:
    df = pd.read_csv(file_path)  
except Exception as e:
    raise Exception(f"Error loading CSV file: {e}")

# แปลงคอลัมน์ Throughput mill (kg/h) เป็นตัวเลข ถ้ามีปัญหาจะใช้ NaN แทน
df['Throughput mill (kg/h)'] = pd.to_numeric(df['Throughput mill (kg/h)'], errors='coerce')

class ProductRequest(BaseModel):
    product_name: str

@app.post("/rank_product/")
def rank_product(request: ProductRequest):
    product_name = request.product_name.upper()  # เปลี่ยนเป็น Upercase ให้หมดก่อนนำไปหา

    logging.debug(f"Received product name: {product_name}")

    if product_name not in df['Product'].str.upper().values: 
        raise HTTPException(status_code=404, detail=f"No data found for product: {product_name}")

    product_df = df[df['Product'].str.upper() == product_name].copy()  

    if product_df.empty:
        raise HTTPException(status_code=404, detail=f"No data found for product: {product_name}")

    result = {"extrude": {}, "mill": {}}

    # มีค่า / ใน Column RFT-ext. และ RFT-Mill
    filtered_df = product_df[(product_df['RFT-ext.'] == '/') & (product_df['RFT-Mill'] == '/')].copy()

    logging.debug(f"Filtered DataFrame: {filtered_df}")

    if filtered_df.empty:
        raise HTTPException(status_code=404, detail="No matching data found for both RFT-ext. and RFT-Mill.")

    filtered_df = filtered_df.dropna(subset=['Throughput mill (kg/h)'])

    if filtered_df.empty:
        return {"warning": "No valid numerical data found in 'Throughput mill (kg/h)' column. Skipping throughput ranking."}
    
    # หยิบค่ามากที่สุดของ Throughput mill (kg/h)
    filtered_sorted = filtered_df.sort_values(by='Throughput mill (kg/h)', ascending=False)

    logging.debug(f"Sorted DataFrame: {filtered_sorted}")

    top_entry = filtered_sorted.iloc[0].dropna(how='all')

    logging.debug(f"Top Entry: {top_entry}")

    def filter_parameters(params):
        def round_tens(value):
            if isinstance(value, (int, float)):
                return int(np.round(value / 10.0) * 10)
            return value  # ถ้าไม่ใช่ตัวเลข ให้คืนค่าดั้งเดิม

        def round_torque(value):
            if isinstance(value, (int, float)):
                return 5 * round(value / 5)
            return value  # ถ้าไม่ใช่ตัวเลข ให้คืนค่าดั้งเดิม

        for key, value in params.items():
            if pd.notna(value):
                # แปลงค่าจาก string ให้เป็นตัวเลขถ้าจำเป็น
                if isinstance(value, str) and value.isdigit():
                    value = float(value)

                # ตรวจสอบและปัดค่าเฉพาะในพารามิเตอร์ที่ระบุ
                if key in ['HT1', 'HT2', 'HT3', 'HT4', 'HT5', 'Screw speed']:
                    params[key] = round_tens(value)
                elif key == 'Torque':
                    params[key] = round_torque(value)
                else:
                    params[key] = value  # ค่าพารามิเตอร์อื่นๆ ให้คงเดิม
                
        return params

    # คืน Extrude และข้อมูลใน Column ต่างๆของมัน 
    result["extrude"]["Machine no."] = top_entry.get('Line', 'N/A')
    extrude_params = top_entry[['Dosing', 'Suggestion Side feed', 'HT1', 'HT2', 'HT3', 'HT4', 'HT5', 'Screw speed', 'Torque']].to_dict()
    result["extrude"]["Parameters"] = filter_parameters(extrude_params)

    # คืน Mill และข้อมูลใน Column ต่างๆของมัน 
    result["mill"]["Machine no."] = top_entry.get('Mill', 'N/A')
    mill_params = top_entry[['Feed', 'Sep.', 'Rotor', 'Air flow']].to_dict() 
    result["mill"]["Parameters"] = filter_parameters(mill_params)

    # คืน Throughput
    result["mill"]["Throughput"] = top_entry['Throughput mill (kg/h)']

    logging.debug(f"Result: {result}")

    return result

class MultipleProductRequest(BaseModel):
    product_codes: List[str]
    
# เก็บข้อมูล JSON ชั่วคราวด้วย UUID
json_store = {}

@app.post("/rank_best_process/")
def rank_best_process(request: MultipleProductRequest):
    result = {}

    for product_name in request.product_codes:
        product_name = product_name.upper()
        logging.debug(f"Processing product name: {product_name}")

        if product_name not in df['Product'].str.upper().values: 
            result[product_name] = {"error": f"No data found for product: {product_name}"}
            continue

        product_df = df[df['Product'].str.upper() == product_name].copy()

        # มีค่า / ใน Column RFT-ext. และ RFT-Mill
        filtered_df = product_df[(product_df['RFT-ext.'] == '/') & (product_df['RFT-Mill'] == '/')].copy()

        if filtered_df.empty:
            result[product_name] = {"error" : "No Data pass RFT"}
            continue

        filtered_df = filtered_df.dropna(subset=['Throughput mill (kg/h)'])

        if filtered_df.empty:
            result[product_name] = {"warning" : "Thoruhtput is error"}
            continue

        filtered_sorted = filtered_df.sort_values(by='Throughput mill (kg/h)', ascending=False)
        top_entry = filtered_sorted.iloc[0]

        # ดึง Process order จาก Column 3 (PO)
        best_process_order = top_entry['PO']

        result[product_name] = best_process_order

    # สร้าง UUID เพื่อใช้สำหรับ session นี้
    json_id = str(uuid.uuid4())
    json_store[json_id] = result  # เก็บข้อมูล JSON ไว้ในหน่วยความจำ

    # คืนลิงก์ URL สำหรับดึงข้อมูล JSON
    download_link = f"https://web-production-6f0b.up.railway.app/download-json/{json_id}"
    return {"download_link": download_link}

# Endpoint สำหรับให้เพื่อนร่วมงานเข้ามาดาวน์โหลด JSON ผ่าน UUID
@app.get("/download-json/{json_id}")
def download_json(json_id: str):
    if json_id not in json_store:
        raise HTTPException(status_code=404, detail="JSON data not found")
    
    return JSONResponse(content=json_store[json_id])

