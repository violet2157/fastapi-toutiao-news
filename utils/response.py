from fastapi.responses import JSONResponse
from fastapi.encoders import  jsonable_encoder
def success_response(message:str = "success",data=None):
    content = {
        "code":200,
        "message":message,
        "data":data
    }
    #目标：把任何的fastapi，pydantic，orm对象都能正常响应

    return JSONResponse(content=jsonable_encoder(content),status_code=200)