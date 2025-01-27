from pydantic import BaseModel
from typing import List

class Item(BaseModel):
    product_id: int
    quantity: int

class OrderCreate(BaseModel):
    customer: str
    items: List[Item]
    total_price: float = 0

class OrderResponse(BaseModel):
    order_id: int
    customer: str
    items: List[Item]
    total_price: float