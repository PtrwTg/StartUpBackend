from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from starlette.responses import FileResponse  # นำเข้า FileResponse จาก starlette
import pandas as pd
import numpy as np
from pydantic import BaseModel
import os
import logging
from typing import List, Dict
from fastapi.responses import JSONResponse, StreamingResponse
import uuid  # ใช้ UUID
import json  # ใช้สำหรับแปลงสตริงเป็น JSON
import httpx
from tempfile import NamedTemporaryFile
from io import BytesIO

app = FastAPI()


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

class ProductCode(BaseModel):
    code: str

class ProductListRequest(BaseModel):
    product: List[ProductCode]

# เก็บข้อมูล JSON ชั่วคราวด้วย UUID
json_store = {}

@app.post("/rank_best_process/")
def rank_best_process(request: ProductListRequest):
    result = {"product": []}

    for product in request.product:
        product_name = product.code.upper()
        logging.debug(f"Processing product name: {product_name}")

        if product_name not in df['Product'].str.upper().values:
            logging.error(f"No data found for product: {product_name}")
            continue

        product_df = df[df['Product'].str.upper() == product_name].copy()

        # กรองข้อมูลเฉพาะที่มีค่า '/' ใน RFT-ext. และ RFT-Mill
        filtered_df = product_df[(product_df['RFT-ext.'] == '/') & (product_df['RFT-Mill'] == '/')].copy()

        if filtered_df.empty:
            logging.error(f"No Data pass RFT for product: {product_name}")
            continue

        # กรองข้อมูลที่มีค่า Throughput mill (kg/h)
        filtered_df = filtered_df.dropna(subset=['Throughput mill (kg/h)'])

        if filtered_df.empty:
            logging.warning(f"Thoruhtput is error for product: {product_name}")
            continue

        # เรียงลำดับตาม Throughput mill (kg/h) และเลือกอันที่ดีที่สุด
        filtered_sorted = filtered_df.sort_values(by='Throughput mill (kg/h)', ascending=False)
        top_entry = filtered_sorted.iloc[0]

        # ดึง Process order จาก Column 3 (PO)
        best_process_order = top_entry['PO']

        # เพิ่มข้อมูลใน JSON ที่จะส่งกลับ
        result["product"].append({"code": product_name, "po": best_process_order})

    return result

# Endpoint สำหรับให้คุณติ่งเข้ามาดาวน์โหลด JSON ผ่าน UUID
@app.get("/download-json/{json_id}")
def download_json(json_id: str):
    if json_id not in json_store:
        raise HTTPException(status_code=404, detail="JSON data not found")
    
    return JSONResponse(content=json_store[json_id])

@app.post("/rank_best_process_string/")
async def rank_best_process_string(request: Request):
    try:
        request_body = await request.body()
        request_json = json.loads(request_body.decode('utf-8'))
        product_list_request = ProductListRequest(**request_json)
        return rank_best_process(product_list_request)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON format: {e}")

# ตัวแปรเพื่อเก็บผลลัพธ์ JSON ranked_data
ranked_data = None

@app.get("/fetch_external_data/")
async def fetch_external_data():
    global ranked_data  # ใช้ตัวแปร global เพื่อเก็บผลลัพธ์ JSON ranked_data
    url = "http://182.52.113.42:8080/ssa/production/wip_new/extruder_control/fetch_link.php"
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            response.raise_for_status()  # ตรวจสอบว่าการร้องขอสำเร็จหรือไม่
            data = response.json()  # แปลงข้อมูลที่ได้เป็น JSON
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"HTTP error occurred: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred: {e}")

    # ตรวจสอบว่าข้อมูลที่ได้เป็นสตริง JSON หรือไม่
    if isinstance(data, str):
        data = json.loads(data)  # แปลงสตริง JSON เป็นออบเจ็กต์ Python

    # แปลงข้อมูลให้อยู่ในรูปแบบ JSON ที่ต้องการ
    transformed_data = {"product": [{"code": item["code"]} for item in data["product"]]}

    # เรียกใช้ API /rank_best_process/ ด้วยข้อมูลที่แปลงแล้ว
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post("http://localhost:8000/rank_best_process/", json=transformed_data)
            response.raise_for_status()
            ranked_data = response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"HTTP error occurred while ranking: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred while ranking: {e}")

    return ranked_data

@app.get("/")
async def get_ranked_data():
    global ranked_data  # ใช้ตัวแปร global เพื่อเก็บผลลัพธ์ JSON ranked_data
    ranked_data = await fetch_external_data()  # เรียกใช้ API /fetch_external_data/ เพื่ออัปเดต ranked_data
    if ranked_data is None:
        raise HTTPException(status_code=404, detail="No ranked data available. Please fetch external data first.")
    return ranked_data


# @app.post("/upload-extrude/")
# @app.post("/upload-mill/")

@app.post("/upload-parameter/")
async def upload_parameter(file: UploadFile = File(...), sheet_name: str = 'Sheet1'):
    # ตรวจสอบประเภทไฟล์
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Please upload a valid Excel file with .xlsx or .xls extension")

    # อ่านไฟล์โดยตรงจากไบต์
    try:
        contents = await file.read()
        excel_data = BytesIO(contents)
        
        # เลือก engine ตามประเภทไฟล์
        if file.filename.endswith('.xls'):
            df = pd.read_excel(excel_data, sheet_name=sheet_name, engine='xlrd')
        else:
            df = pd.read_excel(excel_data, sheet_name=sheet_name, engine='openpyxl')
            
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error reading the Excel file: {e}")

    # เลือกคอลัมน์ที่ต้องการและเปลี่ยนชื่อ
    columns_to_keep = {
        'Batch no.': 'Batch no.',
        'Process no.': 'PO',
        'Product code': 'Product',
        'Line': 'Line',
        'Mill-1': 'Mill',
        'Extrusion (Dosing)': 'Dosing',
        'Extrusion (Side feed)': 'Suggestion Side feed',
        'HT1 (C)': 'HT1',
        'HT2 (C)': 'HT2',
        'HT3 (C)': 'HT3',
        'HT4 (C)': 'HT4',
        'HT5 (C)': 'HT5',
        'Screw speed (rpm)': 'Screw speed',
        'Torque (%)': 'Torque',
        'Milling-1 (Feed)': 'Feed',
        'Milling-1 (Sep.)': 'Sep.',
        'Milling-1 (Rotor)': 'Rotor',
        'Milling-1 (Air flow)': 'Air flow'
    }

    # กรองและเปลี่ยนชื่อคอลัมน์
    try:
        df = df[list(columns_to_keep.keys())]
        df = df.rename(columns=columns_to_keep)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Missing required columns: {e}")

   # ล้างข้อมูลที่ไม่จำเป็นออกจากคอลัมน์ทั้งหมด
    df = df.applymap(lambda x: x.strip() if isinstance(x, str) else x)

# บันทึกไฟล์ CSV ชั่วคราวด้วยการเข้ารหัส 'utf-8-sig'
    temp_file = NamedTemporaryFile(delete=False, suffix=".csv")
    df.to_csv(temp_file.name, index=False, encoding='utf-8-sig')

    # ส่งไฟล์ CSV กลับเป็นการตอบกลับ
    temp_file.seek(0)
    return StreamingResponse(
        iter([temp_file.read()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=cleaned_parameter.csv"}
    )

@app.post("/upload-extrude/")
async def upload_extrude(file: UploadFile = File(...)):
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Please upload a valid Excel file with .xlsx or .xls extension")

    try:
        contents = await file.read()
        excel_data = BytesIO(contents)
        df = pd.read_excel(excel_data, header=2)  # ใช้ row ที่ 3 เป็น header
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error reading the Excel file: {e}")

    # เลือกคอลัมน์ที่ต้องการและเปลี่ยนชื่อ
    df_cleaned = df[['ProcessOrderId', 'ActualThroughput_AvgWeighted']]
    df_cleaned.columns = ['PO', 'Throughput ext.(kg/h)']

    # ล้างข้อมูลที่ไม่จำเป็นออกจากคอลัมน์
    df_cleaned = df_cleaned.applymap(lambda x: x.strip() if isinstance(x, str) else x)

    # บันทึกไฟล์ CSV ชั่วคราว
    temp_file = NamedTemporaryFile(delete=False, suffix=".csv")
    df_cleaned.to_csv(temp_file.name, index=False, encoding='utf-8-sig')

    # ส่งไฟล์ CSV กลับเป็นการตอบกลับ
    temp_file.seek(0)
    return StreamingResponse(
        iter([temp_file.read()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=cleaned_extrude.csv"}
    )

@app.post("/upload-mill/")
async def upload_mill(file: UploadFile = File(...)):
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Please upload a valid Excel file with .xlsx or .xls extension")

    try:
        contents = await file.read()
        excel_data = BytesIO(contents)
        df = pd.read_excel(excel_data, header=2)  # ใช้ row ที่ 3 เป็น header
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error reading the Excel file: {e}")

    # เลือกคอลัมน์ที่ต้องการและเปลี่ยนชื่อ
    df_cleaned = df[['ProcessOrderId', 'ActualThroughput_AvgWeighted']]
    df_cleaned.columns = ['PO', 'Throughput mill (kg/h)']

    # ล้างข้อมูลที่ไม่จำเป็นออกจากคอลัมน์
    df_cleaned = df_cleaned.applymap(lambda x: x.strip() if isinstance(x, str) else x)

    # บันทึกไฟล์ CSV ชั่วคราว
    temp_file = NamedTemporaryFile(delete=False, suffix=".csv")
    df_cleaned.to_csv(temp_file.name, index=False, encoding='utf-8-sig')

    # ส่งไฟล์ CSV กลับเป็นการตอบกลับ
    temp_file.seek(0)
    return StreamingResponse(
        iter([temp_file.read()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=cleaned_mill.csv"}
    )

@app.post("/upload-qapd/")
async def upload_qapd(file: UploadFile = File(...)):
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Please upload a valid Excel file with .xlsx or .xls extension")

    try:
        contents = await file.read()
        excel_data = BytesIO(contents)
        # อ่านข้อมูลจากชีทที่ชื่อว่า 'Data 2023-2024'
        df = pd.read_excel(excel_data, sheet_name='Data 2023-2024', header=0)  # ใช้ row แรกเป็น header
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error reading the Excel file: {e}")

    # เลือกคอลัมน์ที่ต้องการ
    df_cleaned = df[['Work Order no.', 'Granule', 'Defect (NCR)']]

    # ประมวลผลข้อมูลตามเงื่อนไข
    df_cleaned['Granule'] = df_cleaned['Granule'].apply(lambda x: '' if pd.notnull(x) else '/')
    df_cleaned['Defect (NCR)'] = df_cleaned['Defect (NCR)'].apply(lambda x: '' if pd.notnull(x) else '/')

    # เปลี่ยนชื่อคอลัมน์
    df_cleaned.columns = ['PO', 'RFT-ext.', 'RFT-Mill']

    # ลบ Rows ที่มีค่า PO เป็นค่าว่าง
    df_cleaned = df_cleaned[df_cleaned['PO'] != '']

    # บันทึกไฟล์ CSV ชั่วคราว
    temp_file = NamedTemporaryFile(delete=False, suffix=".csv")
    df_cleaned.to_csv(temp_file.name, index=False, encoding='utf-8-sig')

    # ส่งไฟล์ CSV กลับเป็นการตอบกลับ
    temp_file.seek(0)
    return StreamingResponse(
        iter([temp_file.read()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=cleaned_qapd.csv"}
    )




