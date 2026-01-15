from sqlalchemy import Column, Integer, String, Float, DateTime, func, BigInteger
from .database import Base

class Transacao(Base):
    __tablename__ = "transacoes"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, index=True)
    descricao = Column(String)
    valor = Column(Float)
    categoria = Column(String)
    metodo_pagamento = Column(String)
    tipo = Column(String)
    data = Column(DateTime(timezone=True), server_default=func.now())

class Meta(Base):
    __tablename__ = "metas"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, index=True)
    categoria = Column(String)
    valor_limite = Column(Float)