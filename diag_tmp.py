import iq_service
import analysis

iq_service.connect()

for strategy in analysis.STRATEGIES:
    print('=' * 50)
    print('ESTRATEGIA:', strategy)
    for expiry in ('1min', '5min', '15min'):
        interval = analysis.TIMEFRAMES[expiry]['interval']
        raw = iq_service.get_candles_smart('EURUSD', interval, 240)
        candles = analysis._drop_incomplete_candle(raw, interval)
        df = analysis.compute_indicators(analysis.candles_to_df(candles))
        print('---', expiry, 'linhas:', len(df), '---')
        if df.empty:
            print('  SEM DADOS')
            continue
        decision = analysis._strategy_signal(df, strategy)
        row = df.iloc[-1]
        print('  sinal:', decision['signal'], '| score:', decision['score'])
        print('  motivo:', decision['reason'])
        print('  EMA20:', round(float(row['EMA20']), 5), 'EMA50:', round(float(row['EMA50']), 5))
        print('  ADX:', round(float(row['ADX']), 1), 'RSI:', round(float(row['RSI']), 1), 'ATR:', round(float(row['ATR']), 5))
        print('  close:', round(float(row['Close']), 5), 'open:', round(float(row['Open']), 5))
