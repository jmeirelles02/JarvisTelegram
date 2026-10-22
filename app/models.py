from sqlalchemy import Column, Integer, String, Float, DateTime, func, BigInteger, Boolean, Text, UniqueConstraint
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


class PreferenciasPessoais(Base):
    __tablename__ = "preferencias_pessoais"
    user_id = Column(BigInteger, primary_key=True)
    fuso = Column(String, nullable=False, default="America/Sao_Paulo")
    horario_resumo = Column(String)


class MensagemConversa(Base):
    __tablename__ = "mensagens_conversa"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    papel = Column(String(10), nullable=False)
    texto = Column(Text, nullable=False)
    criada_em = Column(DateTime(timezone=True), nullable=False, index=True)


class PerfilAssistente(Base):
    __tablename__ = "perfis_assistente"
    user_id = Column(BigInteger, primary_key=True)
    proativo = Column(Boolean, nullable=False, default=True)
    inicio_contato = Column(String(5), nullable=False, default="08:00")
    fim_contato = Column(String(5), nullable=False, default="21:00")
    limite_diario = Column(Integer, nullable=False, default=2)
    versao_contexto = Column(Integer, nullable=False, default=0)
    ultima_analise = Column(DateTime(timezone=True))


class MemoriaPessoal(Base):
    __tablename__ = "memorias_pessoais"
    __table_args__ = (UniqueConstraint("user_id", "chave"),)
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    chave = Column(String(50), nullable=False)
    tipo = Column(String(20), nullable=False)
    texto = Column(String(500), nullable=False)
    concluido = Column(Boolean, nullable=False, default=False)
    atualizada_em = Column(DateTime(timezone=True), nullable=False)


class Lembrete(Base):
    __tablename__ = "lembretes"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    descricao = Column(String(500), nullable=False)
    proxima_data = Column(DateTime(timezone=True), index=True)
    fuso = Column(String, nullable=False)
    repeticao = Column(String, nullable=False, default="nenhuma")
    dia_original = Column(Integer, nullable=False)
    ativo = Column(Boolean, nullable=False, default=True)


class EntregaPessoal(Base):
    __tablename__ = "entregas_pessoais"
    id = Column(Integer, primary_key=True)
    chave = Column(String(200), nullable=False, unique=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    lembrete_id = Column(Integer, index=True)
    tipo = Column(String, nullable=False, default="lembrete")
    texto = Column(Text, nullable=False)
    estado = Column(String, nullable=False, default="pendente", index=True)
    tentativas = Column(Integer, nullable=False, default=0)
    tentar_em = Column(DateTime(timezone=True), nullable=False, index=True)
    expira_em = Column(DateTime(timezone=True))


class EventoPendente(Base):
    __tablename__ = "eventos_pendentes"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    uid = Column(String, nullable=False, unique=True)
    titulo = Column(String(500), nullable=False)
    inicio = Column(DateTime(timezone=True), nullable=False)
    fim = Column(DateTime(timezone=True), nullable=False)
    aviso_minutos = Column(Integer, nullable=False, default=30)
    expira_em = Column(DateTime(timezone=True), nullable=False)
    estado = Column(String, nullable=False, default="pendente")
    calendario = Column(String, nullable=False)
