"""Smoke test da lógica de fatura/parcelas: python test_parcelamento.py"""
from datetime import datetime
from dateutil.relativedelta import relativedelta

import run_bot

hoje = datetime.now()

for dia in (1, 7, 15, 28, 31):
    data = run_bot._proxima_data_fatura(dia)
    assert data > hoje, f"dia {dia}: {data} não é futura"
    assert data <= hoje + relativedelta(months=1, day=31), f"dia {dia}: {data} longe demais"
    ultimo_dia_do_mes = (data + relativedelta(day=31)).day
    assert data.day == min(dia, ultimo_dia_do_mes), f"dia {dia}: caiu em {data}"

print("OK")
