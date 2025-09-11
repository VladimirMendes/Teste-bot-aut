import time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from iqoptionapi.stable_api import IQ_Option

# =========================
# 🔎 1. Conexão e Inicialização
# =========================
EMAIL = "SEU_EMAIL"
SENHA = "SUA_SENHA"
PAR = "EURUSD-OTC"
TIMEFRAME = 5  # 1 = M1, 5 = M5
STOP_WIN_PCT = 0.10   # 10%
STOP_LOSS_PCT = 0.30  # 30%
ENTRY_PCT = 0.05      # 5% do saldo
TRAILING_STEP = 0.03  # 3% a cada degrau
MIN_ENTRY = 3         # valor mínimo de entrada

API = IQ_Option(EMAIL, SENHA)
API.connect()

if not API.check_connect():
    print("❌ Erro na conexão!")
    exit()

print("🛜 Conectado à IQ Option com sucesso!")

saldo_inicial = API.get_balance()
stop_win = saldo_inicial * (1 + STOP_WIN_PCT)
stop_loss = saldo_inicial * (1 - STOP_LOSS_PCT)
trailing_stop = saldo_inicial
meta_atingida = False
perdas_consecutivas = 0
pausa_ate = None

print(f"\n 💰 Saldo inicial: {saldo_inicial:.2f}")
print(f" 🎯 Meta diária (10%): {stop_win:.2f}")
print(f" ⛔ Stop Loss inicial (30%): {stop_loss:.2f}\n")

# =========================
# 📊 2. Indicadores Técnicos
# =========================
def calcular_indicadores(candles):
    df = pd.DataFrame(candles)
    df['close'] = df['close'].astype(float)

    # EMA 15 e EMA 50
    df['EMA15'] = df['close'].ewm(span=15, adjust=False).mean()
    df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()

    # RSI (12)
    delta = df['close'].diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(12).mean()
    avg_loss = pd.Series(loss).rolling(12).mean()
    rs = avg_gain / avg_loss
    df['RSI'] = 100 - (100 / (1 + rs))

    # Bollinger Bands (20, 2)
    df['SMA20'] = df['close'].rolling(20).mean()
    df['STD20'] = df['close'].rolling(20).std()
    df['Upper'] = df['SMA20'] + 2 * df['STD20']
    df['Lower'] = df['SMA20'] - 2 * df['STD20']

    # ATR (14)
    df['H-L'] = df['max'] - df['min']
    df['H-C'] = abs(df['max'] - df['close'].shift())
    df['L-C'] = abs(df['min'] - df['close'].shift())
    df['TR'] = df[['H-L', 'H-C', 'L-C']].max(axis=1)
    df['ATR'] = df['TR'].rolling(14).mean()

    return df.iloc[-1]

# =========================
# 🧠 3. Lógica de Decisão
# =========================
def gerar_sinal(indicadores):
    sinais = []

    # RSI
    if indicadores['RSI'] < 30:
        sinais.append("call")
    elif indicadores['RSI'] > 70:
        sinais.append("put")

    # EMAs
    if indicadores['EMA15'] > indicadores['EMA50']:
        sinais.append("call")
    elif indicadores['EMA15'] < indicadores['EMA50']:
        sinais.append("put")

    # Bollinger
    close = indicadores['close']
    if close <= indicadores['Lower'] and indicadores['EMA15'] > indicadores['EMA50']:
        sinais.append("call")
    elif close >= indicadores['Upper'] and indicadores['EMA15'] < indicadores['EMA50']:
        sinais.append("put")

    # ATR filtro (mercado parado = sem trade)
    if indicadores['ATR'] < 0.00005:
        return None

    # Regras finais
    if sinais.count("call") >= 2:
        return "call"
    elif sinais.count("put") >= 2:
        return "put"
    return None

# =========================
# 💰 4. Gestão de Risco
# =========================
def calcular_valor_entrada(saldo_atual):
    return max(saldo_atual * ENTRY_PCT, MIN_ENTRY)

# =========================
# ⚙️ 5. Execução do Trade
# =========================
def executar_trade(direcao, valor):
    global perdas_consecutivas
    status, id = API.buy_digital_spot_v2(PAR, valor, direcao, TIMEFRAME)

    if not status:
        print("❌ Erro ao enviar ordem!")
        return 0

    while True:
        check, lucro = API.check_win_digital_v2(id)
        if check:
            break
        time.sleep(1)

    if lucro > 0:
        perdas_consecutivas = 0
        print(f"✅ WIN | Lucro: {lucro:.2f}")
    else:
        perdas_consecutivas += 1
        print(f"❌ LOSS | Prejuízo: {lucro:.2f}")

    return lucro

# =========================
# 🔂 6. Loop Automático
# =========================
while True:
    agora = datetime.now()

    # horário permitido (09:00 - 18:00)
    if not (9 <= agora.hour < 18):
        print("⏸ Fora do horário de operação...")
        time.sleep(60)
        continue

    # pausa após 3 perdas
    if pausa_ate and agora < pausa_ate:
        print(f"⏸ Pausado até {pausa_ate.strftime('%H:%M:%S')}")
        time.sleep(30)
        continue

    # saldo e metas
    saldo_atual = API.get_balance()

    # stop loss e meta diária
    if saldo_atual <= stop_loss:
        print("⛔ Stop Loss atingido. Pausando até amanhã.")
        pausa_ate = datetime.now().replace(hour=9, minute=0, second=0) + timedelta(days=1)
        continue
    if saldo_atual >= stop_win:
        print("🎯 Meta diária atingida. Pausando até amanhã.")
        pausa_ate = datetime.now().replace(hour=9, minute=0, second=0) + timedelta(days=1)
        continue

    # trailing stop
    if saldo_atual > trailing_stop * (1 + TRAILING_STEP):
        trailing_stop = saldo_atual
        stop_loss = trailing_stop * (1 - TRAILING_STEP)
        print(f"🔒 Trailing Stop atualizado → Stop Loss: {stop_loss:.2f}")

    # buscar candles
    candles = API.get_candles(PAR, 60 * TIMEFRAME, 100, time.time())
    indicadores = calcular_indicadores(candles)
    sinal = gerar_sinal(indicadores)

    if not sinal:
        print(f"[{agora.strftime('%H:%M:%S')}] ⏸ Nenhum sinal detectado.")
    else:
        valor = calcular_valor_entrada(saldo_atual)
        print(f"[{agora.strftime('%H:%M:%S')}] 🎯 Sinal: {sinal.upper()} | Entrada: {valor:.2f}")
        lucro = executar_trade(sinal, valor)
        saldo_final = API.get_balance()
        print(f"📊 Saldo atual: {saldo_final:.2f}")

        if perdas_consecutivas >= 3:
            pausa_ate = datetime.now() + timedelta(minutes=30)
            print("⏸ Pausa de 30 minutos (3 perdas seguidas).")

    time.sleep(60 * TIMEFRAME)
