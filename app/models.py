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

class Parcelamento(Base):
    __tablename__ = "parcelamentos"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, index=True)
    descricao = Column(String)
    valor_parcela = Column(Float)
    categoria = Column(String)
    metodo_pagamento = Column(String)
    parcelas_total = Column(Integer)
    parcelas_pagas = Column(Integer, default=0)
    proxima_data = Column(DateTime(timezone=True))

class ConfigFatura(Base):
    __tablename__ = "config_fatura"
    user_id = Column(BigInteger, primary_key=True)
    dia_fechamento = Column(Integer)

class Assinatura(Base):
    __tablename__ = "assinaturas"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, index=True)
    descricao = Column(String)
    valor = Column(Float)
    categoria = Column(String)
    metodo_pagamento = Column(String)
    proxima_data = Column(DateTime(timezone=True))

class Meta(Base):
    __tablename__ = "metas"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, index=True)
    categoria = Column(String)
    valor_limite = Column(Float)