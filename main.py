from fastapi import FastAPI, HTTPException
import pandas as pd
import numpy as np
from pydantic import BaseModel
import os
from fastapi.middleware.cors import CORSMiddleware

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

@app.post("/rank_product/") #api หา Process order ที่ต้องการ โดยมันจะต้องมีค่า / ใน RFT-ext.และ RFT-Mill หลังจากนั้นให้หา Rows ที่มีค่าใน Throughput mill (kg/h) มากที่สุด 
# หลังจากนั้นจึงคืนค่าใน Row นั้นกลับไป โดยค่าที่คืนจะแบ่งเป็น Extrude , Mill และ  Throughput  โดย โดยใน Extrude และ Mill จะมีข้อมูล Machine no.
# และ parameter ระบุอยู่ ส่วน Throughput จะเป็นข้อมูลจาก Column Throughput mill (kg/h) ที่ค้นเจอเฉยๆ 
def rank_product(request: ProductRequest):
    product_name = request.product_name.upper()  # เปลี่ยนเป็น Upercase ให้หมดก่อนนำไปหา

    if product_name not in df['Product'].str.upper().values: 
        raise HTTPException(status_code=404, detail=f"No data found for product: {product_name}")

    product_df = df[df['Product'].str.upper() == product_name].copy()  

    if product_df.empty:
        raise HTTPException(status_code=404, detail=f"No data found for product: {product_name}")

    result = {"extrude": {}, "mill": {}}

    # มีค่า  / ใน Column RFT-ext. และ RFT-Mill
    filtered_df = product_df[(product_df['RFT-ext.'] == '/') & (product_df['RFT-Mill'] == '/')].copy()

    if filtered_df.empty:
        raise HTTPException(status_code=404, detail="No matching data found for both RFT-ext. and RFT-Mill.")

    filtered_df = filtered_df.dropna(subset=['Throughput mill (kg/h)'])

    if filtered_df.empty:
        return {"warning": "No valid numerical data found in 'Throughput mill (kg/h)' column. Skipping throughput ranking."}
    # หยิบค่ามากที่สุดของ Throughput mill (kg/h) 
    filtered_sorted = filtered_df.sort_values(by='Throughput mill (kg/h)', ascending=False)

    top_entry = filtered_sorted.iloc[0].dropna(how='all')

    def filter_parameters(params):
        return {key: (int(value) if isinstance(value, (np.integer, np.int64)) else float(value) if isinstance(value, np.float64) else value)
                for key, value in params.items() if pd.notna(value)}

#คืน Extrude และข้อมูลใน Column ต่างๆของมัน 
    result["extrude"]["Machine no."] = top_entry.get('Line', 'N/A')
    extrude_params = top_entry[['Dosing', 'Suggestion Side feed', 'HT1', 'HT2', 'HT3', 'HT4', 'HT5', 'Screw speed', 'Torque', #'Outlet temp'ยกเลิก parameter นี้ 
                                ]].to_dict()
    result["extrude"]["Parameters"] = filter_parameters(extrude_params)

#คืน Mill และข้อมูลใน Column ต่างๆของมัน 
    result["mill"]["Machine no."] = top_entry.get('Mill', 'N/A')
    mill_params = top_entry[['Feed', 'Sep.', 'Rotor', 'Air flow', #'Inlet temp.', 'Outlet temp.', 'FG. temp.' ยกเลิก parameter นี้ 
                             ]].to_dict() 
    result["mill"]["Parameters"] = filter_parameters(mill_params)

    # คืน Throughput
    result["mill"]["Throughput"] = top_entry['Throughput mill (kg/h)']

    return result

