from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.database import SessionLocal, engine, Base
from typing import List, Optional
from backend.models import Prenda, Order
from backend.schemas import OrderCreate, OrderResponse, InventoryUpdate
import logging, openai, json, os
from dotenv import load_dotenv


app = FastAPI()

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
Base.metadata.create_all(bind=engine)
openai.api_key = os.getenv("OPENAI_KEY")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def parse_user_message(message: str) -> dict:
    try:
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """
                        Eres un asistente que interpreta comandos para gestionar pedidos y stock. 
                        Devuelve un JSON válido con las siguientes claves:
                        - "intent": "crear_pedido", "consultar_stock", o "desconocido".
                        - "product_id": número (solo si aplica).
                        - "quantity": número (solo si aplica).
                        
                        Ejemplos:
                        - Mensaje: "Crear pedido de 5 productos de id 90"
                        Respuesta: {"intent": "crear_pedido", "product_id": 90, "quantity": 5}
                        
                        - Mensaje: "Consultar stock de id 90"
                        Respuesta: {"intent": "consultar_stock", "product_id": 90}
                    """
                },
                {"role": "user", "content": message}
            ]
        )
        
        json_str = response.choices[0].message.content.strip()
        parsed_data = json.loads(json_str)
        
        if "intent" not in parsed_data:
            return {"intent": "desconocido"}
        
        return parsed_data
    
    except json.JSONDecodeError:
        logging.error("Respuesta no es un JSON válido")
        return {"intent": "desconocido"}
    
    except Exception as e:
        logging.error(f"Error al parsear mensaje: {e}")
        return {"intent": "desconocido"}
    
@app.post("/chat")
def chat_with_agent(message: str, db: Session = Depends(get_db)):
    parsed_data = parse_user_message(message)#recibir mensaje del user
    
    #en las siguientes identificamos cual es la intención del usuario y en base a eso hacemos los llamados
    #a la api según el caso
    if parsed_data["intent"] == "crear_pedido":
        product_id = parsed_data.get("product_id")
        quantity = parsed_data.get("quantity")
        
        if not product_id or not quantity: #si es que falta alguno de los dos... 
            return {"error": "Faltan parámetros (product_id o quantity)"}
        
        try: #creamos la orden
            order = OrderCreate(
                customer="Cliente desde IA",
                items=[{"product_id": product_id, "quantity": quantity}],
                total_price=0
            )
            created_order = create_order(order, db)
            return {"action": "pedido_creado", "order": created_order}
        
        except Exception as e:
            return {"error": str(e)}
    
    elif parsed_data["intent"] == "consultar_stock":
        product_id = parsed_data.get("product_id")
        if not product_id:
            return {"error": "Falta product_id"}
        
        prenda = db.query(Prenda).filter(Prenda.id == product_id).first()
        if not prenda:
            return {"error": f"Producto {product_id} no encontrado"}
        
        return {"action": "stock_consultado", "stock": prenda.cantidad_disponible}
    
    else:
        return {"intent": parsed_data["intent"], "parsed_data": parsed_data}

@app.get("/orders", response_model=List[OrderResponse])
def get_orders(db: Session = Depends(get_db)):
    return db.query(Order).all()

@app.post("/order_create", response_model=OrderResponse)
def create_order(order: OrderCreate, db: Session = Depends(get_db)):
    db_order = Order(**order.model_dump())
    db_order.total_price = 0
    for item in order.items:
        prenda = db.query(Prenda).filter(Prenda.id == item.product_id).first()
        if prenda is None:
            raise HTTPException(status_code=404, detail=f"Product id {item.product_id} not found")
        if not item.quantity > prenda.cantidad_disponible:
            if item.quantity >= 0 and item.quantity <= 49:
                db_order.total_price += item.quantity * prenda.precio_50_u
            elif item.quantity >= 50 and item.quantity <= 99:
                db_order.total_price += item.quantity * prenda.precio_100_u
            else:
                db_order.total_price += item.quantity * prenda.precio_200_u
        else:
            raise HTTPException(status_code=404, detail=f"Product id {item.product_id} not enough stock")
        
    db.add(db_order)
    db.commit()
    db.refresh(db_order)
    print(f"Created order: {db_order}")  
    return db_order

@app.put("/orders/{order_id}", response_model=OrderResponse)
def update_order(order_id: int, db: Session = Depends(get_db)):
    db_order = db.query(Order).filter(Order.order_id == order_id).first()
    if db_order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    db.commit()
    db.refresh(db_order)
    return db_order

@app.get("/inventory")
def get_inventory(product_id: Optional[int] = None, db: Session = Depends(get_db)):
    if product_id:
        prenda = db.query(Prenda).filter(Prenda.id == product_id).first()
        if prenda is None:
            raise HTTPException(status_code=404, detail="Product not found")
        return {"product_id": prenda.id, "stock": prenda.cantidad_disponible}
    inventory = db.query(Prenda).all()
    return [{"product_id": prenda.id, "stock": prenda.cantidad_disponible} for prenda in inventory]

@app.put("/inventory/{product_id}")
def update_inventory(product_id: int, inventory: InventoryUpdate, db: Session = Depends(get_db)):
    prenda = db.query(Prenda).filter(Prenda.id == product_id).first()
    if prenda is None:
        raise HTTPException(status_code=404, detail="Product not found")
    prenda.cantidad_disponible = inventory.stock
    db.commit()
    db.refresh(prenda)
    return {"product_id": prenda.id, "stock": prenda.cantidad_disponible}