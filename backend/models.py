from sqlalchemy import Column, Integer, String, Float, JSON
from database import Base

class Prenda(Base):
    __tablename__ = "prendas"
    id = Column(Integer, primary_key=True, index=True)
    tipo_prenda = Column(String)
    talla = Column(String)
    color = Column(String)
    cantidad_disponible = Column(Integer)
    precio_50_u = Column(Float)
    precio_100_u = Column(Float)
    precio_200_u = Column(Float)
    disponible = Column(String)
    categoria = Column(String)
    descripcion = Column(String)

class Order(Base):
    __tablename__ = "orders_table"
    order_id = Column(Integer, primary_key=True, index=True)
    customer = Column(String)
    items = Column(JSON)
    total_price = Column(Float)