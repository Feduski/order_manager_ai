from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.database import SessionLocal, engine, Base
from typing import List, Optional
from backend.models import Prenda, Order
from backend.schemas import OrderCreate, OrderResponse, InventoryUpdate
import logging, openai, json, os
import requests

app = FastAPI()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
Base.metadata.create_all(bind=engine)

#variables de entorno
openai.api_key = os.getenv("OPENAI_KEY")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") 
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

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
                        Sos un asistente que interpreta comandos para gestionar pedidos y stock. 
                        Devolvé un JSON válido con las siguientes claves:
                        - "intent": "crear_pedido", "consultar_stock", "consultar_pedidos", "pedido_por_id", "actualizar_stock" o "desconocido".
                        - "product_id": número (solo si aplica).
                        - "order_id": número (solo si aplica).
                        - "quantity": número (solo si aplica).
                        
                        Ejemplos:
                        - Mensaje: "Crear pedido de 5 productos de id 90"
                        Respuesta: {"intent": "crear_pedido", "product_id": 90, "quantity": 5}
                        
                        - Mensaje: "Consultar stock de id 90"
                        Respuesta: {"intent": "consultar_stock", "product_id": 90}

                        - Mensaje: "Mostrame todos los pedidos"
                        Respuesta: {"intent": "consultar_pedidos"}

                        - Mensaje: "Mostrame el pedido con id 1"
                        Respuesta: {"intent": "pedido_por_id", "order_id": 1}

                        - Mensaje: "Actualizar stock de item id 23 a 400 unidades"
                        Respuesta: {"intent": "actualizar_stock", "product_id": 23, "quantity": 400}

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

def send_telegram_message(chat_id: int, text: str):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    response = requests.post(url, json=payload)
    if response.status_code != 200:
        logging.error(f"Error enviando mensaje a Telegram: {response.text}")

def parse_response_for_user(info: str) -> dict:
    response = openai.chat.completions.create(
    model="gpt-4o-mini",
    messages=[
        {
            "role": "system",
            "content": """
                Sos un asistente de ventas que responde a consultas sobre pedidos y stock.
                Tenés que devolver un mensaje muy claro para el usuario final, con la información que se te envia en un json.

                Ejemplo:
                - Info recibida: {
                "action": "pedido_creado",
                "order": {"order_id": 12, "customer": "Cliente desde IA","items": [{"product_id": 10,"quantity": 10}],
                "total_price": 6470.0 }}
                - Respuesta esperada: Genial! Confirmada la orden 12 para Cliente desde IA con 10 productos de id 10. El precio total es de $6470.0

                Ejemplo 2:
                - Info recibida: {"action": "stock_consultado", "stock": 20}
                - Respuesta esperada: El stock disponible es de 20 unidades

                Ejemplo 3:
                - Info recibida: {"action": "pedidos_totales", "orders": [{"order_id": 12, "customer": "Cliente desde IA","items": [{"product_id": 10,"quantity": 10}],
                "total_price": 6470.0 }], [{"order_id": 13, "customer": "Cliente desde IA","items": [{"product_id": 10,"quantity": 10}]}
                - Respuesta esperada: Los pedidos totales son: 12 para Cliente desde IA con 10 productos de id 10. El precio total es de $6470.0, 13 para Cliente desde IA con 10 productos de id 10

                Ejemplo 4:
                - Info recibida: {"action": "stock_actualizado", "product_id": 20, "stock": 50}
                - Respuesta esperada: El stock del producto id 20 fue actualizado a 50 unidades
            """
        },
        {"role": "user", "content": info}
    ]
    )
    return response.choices[0].message.content.strip()

@app.post("/webhook")
async def telegram_webhook(update: dict):
    try:
        message = update.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text")
        
        if not chat_id or not text:
            return {"status": "error", "message": "Datos incompletos"}
        
        logging.info(f"Mensaje recibido de {chat_id}: {text}")
        
        with SessionLocal() as db:  
            response = chat_with_agent(text, db)  
            resp_to_user = parse_response_for_user(json.dumps(response))
            send_telegram_message(chat_id, resp_to_user)
        
        return {"status": "success"}
    
    except Exception as e:
        logging.error(f"Error procesando mensaje: {e}")
        send_telegram_message(chat_id, f"Error: {str(e)}")
        return {"status": "error", "message": str(e)}

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

            return {"action": "pedido_creado", "order": {
                "order_id": created_order.order_id,
                "customer": created_order.customer,
                "items": created_order.items,
                "total_price": created_order.total_price
            }}
        
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

    elif parsed_data["intent"] == "consultar_pedidos":
        orders = get_orders(db)
        orders_list = []
        for order in orders:
            order_dict = {
                "order_id": order.order_id,
                "customer": order.customer,
                "items": order.items,
                "total_price": order.total_price
            }
            orders_list.append(order_dict)
            
        return {"action": "pedidos_totales", "orders": orders_list}
    
    elif parsed_data["intent"] == "pedido_por_id":
        order_id = parsed_data.get("order_id")
        if not order_id:
            return {"error": "Falta order_id"}
        
        order = get_order_by_id(order_id, db)
        return {"action": "pedido_consultado", "order": order}
    
    elif parsed_data["intent"] == "actualizar_stock":
        product_id = parsed_data.get("product_id")
        quantity = parsed_data.get("quantity")
        if not product_id or not quantity:
            return {"error": "Faltan parámetros (product_id o quantity)"}
        
        updated_inventory = update_inventory(product_id, quantity, db)
        return {"action": "stock_actualizado", "product_id": updated_inventory['product_id'], "stock": updated_inventory["stock"]}
    
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

            prenda.cantidad_disponible -= item.quantity
        else:
            raise HTTPException(status_code=404, detail=f"Product id {item.product_id} not enough stock")
        
    db.add(db_order)
    db.commit()
    db.refresh(db_order)
    return db_order

@app.get("/orders/{order_id}", response_model=OrderResponse)
def get_order_by_id(order_id: int, db: Session = Depends(get_db)):
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
    inventory = db.query(Prenda).all() #si no dan uno en particular, devolvemos todo el inventario
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