# StartUpBackend

การใช้งาน API 
# @app.post("/rank_product/")
รับข้อมูล Product ที่ส่งมา คืนออกมาเป็น jsonresponse ที่ข้างในมีข้อมูลพารามิเตอร์อยู่ 
# @app.post("/rank_best_process/")
รับข้อมูล Product ที่ส่งมาหลายตัว คืนออกมาเป็น json link ซึ่งข้างในเป็นข้อมูลในรูปแบบ Productcode : Processorder 