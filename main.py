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

# สร้าง dict เพื่อเก็บข้อมูลชั่วคราว
uploaded_files_data = {
    'parameter': None,
    'extrude': None,
    'mill': None,
    'qapd': None,
    "combined": None
}

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

    # แปลงคอลัมน์ที่จำเป็นเป็นตัวเลข
    numeric_columns = ['Line', 'Mill', 'Throughput mill (kg/h)', 'Throughput ext.(kg/h)',
                       'Dosing', 'Suggestion Side feed', 'HT1', 'HT2', 'HT3', 'HT4', 'HT5',
                       'Screw speed', 'Torque', 'Feed', 'Sep.', 'Rotor', 'Air flow']
    for col in numeric_columns:
        if col in top_entry and pd.notnull(top_entry[col]):
            try:
                top_entry[col] = float(top_entry[col])
            except ValueError:
                pass  # ถ้าแปลงไม่ได้ ให้ข้ามไป

    def filter_parameters(params):
        def round_tens(value):
            if pd.isnull(value):
                return value
            try:
                numeric_value = float(value)
                return int(round(numeric_value / 10) * 10)
            except ValueError:
                return value  # ถ้าไม่สามารถแปลงเป็นตัวเลขได้ ให้คืนค่าดั้งเดิม

        def round_torque(value):
            if pd.isnull(value):
                return value
            try:
                numeric_value = float(value)
                if numeric_value % 10 in [3, 4, 6, 7]:
                    return (numeric_value // 10) * 10 + 5
                elif numeric_value % 10 in [1, 2]:
                    return (numeric_value // 10) * 10
                elif numeric_value % 10 in [8, 9]:
                    return (numeric_value // 10) * 10 + 10
                else:
                    return round(numeric_value, 2)
            except ValueError:
                return value  # ถ้าไม่สามารถแปลงเป็นตัวเลขได้ ให้คืนค่าดั้งเดิม

        for key, value in params.items():
            if key in ['HT1', 'HT2', 'HT3', 'HT4', 'HT5', 'Screw speed']:
                params[key] = round_tens(value)
            elif key == 'Torque':
                params[key] = round_torque(value)
            else:
                params[key] = value
        return params

    # คืน Extrude และข้อมูลใน Column ต่างๆของมัน
    result["extrude"]["Machine no."] = int(top_entry.get('Line')) if pd.notnull(top_entry.get('Line')) else 'N/A'
    extrude_params = top_entry[['Dosing', 'Suggestion Side feed', 'HT1', 'HT2', 'HT3', 'HT4', 'HT5', 'Screw speed', 'Torque']].to_dict()
    result["extrude"]["Parameters"] = filter_parameters(extrude_params)

    # คืน Mill และข้อมูลใน Column ต่างๆของมัน
    result["mill"]["Machine no."] = int(top_entry.get('Mill')) if pd.notnull(top_entry.get('Mill')) else 'N/A'
    mill_params = top_entry[['Feed', 'Sep.', 'Rotor', 'Air flow']].to_dict()
    result["mill"]["Parameters"] = filter_parameters(mill_params)

    # คืน Throughput
    result["mill"]["Throughput"] = float(top_entry['Throughput mill (kg/h)']) if pd.notnull(top_entry['Throughput mill (kg/h)']) else None

    # แปลงค่าใน result ให้เป็นประเภทข้อมูลมาตรฐาน
    def convert_values(obj):
        if isinstance(obj, dict):
            return {k: convert_values(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_values(i) for i in obj]
        elif isinstance(obj, (np.integer, np.int64, np.int32)):
            return int(obj)
        elif isinstance(obj, (np.floating, np.float64, np.float32)):
            return float(obj)
        else:
            return obj

    result = convert_values(result)

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
    if (json_id not in json_store):
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
    
    uploaded_files_data['parameter'] = df  

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

    # ลบแถวที่มีค่า Throughput ext.(kg/h) เกิน 2000
    df_cleaned = df_cleaned[df_cleaned['Throughput ext.(kg/h)'] <= 2000]

    # ล้างข้อมูลที่ไม่จำเป็นออกจากคอลัมน์
    df_cleaned = df_cleaned.applymap(lambda x: x.strip() if isinstance(x, str) else x)

    uploaded_files_data['extrude'] = df_cleaned

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

    # ลบแถวที่มีค่า Throughput mill (kg/h) เกิน 2000
    df_cleaned = df_cleaned[df_cleaned['Throughput mill (kg/h)'] <= 2000]

    # ล้างข้อมูลที่ไม่จำเป็นออกจากคอลัมน์
    df_cleaned = df_cleaned.applymap(lambda x: x.strip() if isinstance(x, str) else x)
    uploaded_files_data['mill'] = df_cleaned
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

    uploaded_files_data['qapd'] = df_cleaned

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

@app.post("/combine-files/")
async def combine_files():
    # ตรวจสอบว่าไฟล์ทั้งหมดถูกอัปโหลดและคลีนแล้ว
    if not all(key in uploaded_files_data and uploaded_files_data[key] is not None for key in ['parameter', 'extrude', 'mill', 'qapd']):
        raise HTTPException(status_code=400, detail="All files (parameter, extrude, mill, qapd) must be uploaded and cleaned first.")

    # ดึงข้อมูลจาก dict
    df_parameter = uploaded_files_data['parameter']
    df_extrude = uploaded_files_data['extrude']
    df_mill = uploaded_files_data['mill']
    df_qapd = uploaded_files_data['qapd']

    # รวมข้อมูลโดยใช้คอลัมน์ PO เป็นหลัก
    combined_df = df_parameter.copy()
    combined_df = combined_df.merge(df_extrude, on='PO', how='left')
    combined_df = combined_df.merge(df_mill, on='PO', how='left')
    combined_df = combined_df.merge(df_qapd, on='PO', how='left')

    # ลบข้อมูลซ้ำ
    combined_df = combined_df.drop_duplicates(subset=['PO'])

    # เก็บข้อมูล combined_data ใน uploaded_files_data
    uploaded_files_data['combined_data'] = combined_df

    # บันทึกไฟล์ CSV ชั่วคราว
    temp_file = NamedTemporaryFile(delete=False, suffix=".csv")
    combined_df.to_csv(temp_file.name, index=False, encoding='utf-8-sig')

    # ส่งไฟล์ CSV กลับเป็นการตอบกลับ
    temp_file.seek(0)
    return StreamingResponse(
        iter([temp_file.read()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=combined_data.csv"}
    )

@app.post("/append-combined-data/")
async def append_combined_data():
    # ตรวจสอบว่ามีข้อมูล combined_data ใน uploaded_files_data หรือไม่
    if 'combined_data' not in uploaded_files_data or uploaded_files_data['combined_data'] is None:
        raise HTTPException(status_code=400, detail="ไม่พบข้อมูล combined data กรุณารวมไฟล์ก่อน.")
    try:
        # ใช้ข้อมูล combined_data จาก uploaded_files_data
        combined_data = uploaded_files_data['combined_data']
        # อ่านข้อมูลจากไฟล์ RFT 2024.csv
        rft_file_path = 'RFT 2024.csv'
        if not os.path.exists(rft_file_path):
            raise HTTPException(status_code=404, detail="ไม่พบไฟล์ RFT 2024.csv บนเซิร์ฟเวอร์.")
        
        rft_data = pd.read_csv(rft_file_path)
        # รวมข้อมูลจากทั้งสองไฟล์
        combined_df = pd.concat([rft_data, combined_data], ignore_index=True)
        # บันทึกข้อมูลที่รวมแล้วเป็นไฟล์ RFT 2024.csv
        combined_df.to_csv(rft_file_path, index=False, encoding='utf-8-sig')
        
        # โหลดไฟล์ CSV และอ่านใหม่อีกครั้ง
        global df
        df = pd.read_csv(rft_file_path)
        
        # ตรวจสอบว่าข้อมูลถูกโหลดใหม่หรือไม่
        logging.debug(f"Updated DataFrame: {df.head()}")
        
        return {"detail": "เพิ่มข้อมูลลงใน RFT 2024.csv และโหลดใหม่เรียบร้อยแล้ว."}
    except Exception as e:
        logging.error(f"Error occurred: {e}")
        raise HTTPException(status_code=500, detail=f"เกิดข้อผิดพลาดขณะประมวลผลไฟล์: {e}")
