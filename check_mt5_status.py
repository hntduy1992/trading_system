"""
Standalone CLI tool to check MetaTrader 5 terminal connection and AutoTrading permissions.
"""
import sys

def check_mt5_status():
    print('=' * 70)
    print('   KIEM TRA TRANG THAI TU DONG GIAO DICH (METATRADER 5)')
    print('=' * 70)

    try:
        import MetaTrader5 as mt5
    except ImportError:
        print('[LOI] Chua cai dat thu vien MetaTrader5. Chay: pip install MetaTrader5')
        return False

    print('[*] Dang ket noi toi phan mem MetaTrader 5...')
    if not mt5.initialize():
        print(f'[THAT BAI] Khong the khoi tao ket noi MT5. Ma loi: {mt5.last_error()}')
        print('  -> Vui long dam bao phan mem MT5 dang duoc mo tren may tinh cua ban.')
        return False

    print('[OK] Da ket noi voi phan mem MT5 Terminal.')
    
    term_info = mt5.terminal_info()
    acc_info = mt5.account_info()

    if not term_info:
        print('[LOI] Khong lay duoc thong tin Terminal.')
        mt5.shutdown()
        return False

    print('-' * 70)
    print(f' Terminal: {term_info.name} (Build {term_info.build})')
    print(f' Cong ty : {term_info.company}')
    conn_str = "KET NOI TOT" if term_info.connected else "MAT KET NOI"
    print(f" Ket noi server broker: {conn_str}")
    
    # Check 1: Terminal AlgoTrading Button
    print("\n[1] Kiem tra nut 'Algo Trading' tren thanh cong cu MT5:")
    if term_info.trade_allowed:
        print("    -> [DAT] Nut Algo Trading dang BAT (Mau XANH LA CAY) [trade_allowed=True]")
    else:
        print("    -> [CANH BAO] Nut Algo Trading dang TAT (Mau DO) [trade_allowed=False]!")
        print("       >>> BAM NUT 'Algo Trading' TREN MT5 DE CHUYEN SANG MAU XANH LA <<<")

    # Check 2: Account Info
    if acc_info:
        trade_perm = "CHO PHEP" if acc_info.trade_allowed else "BI CHAN"
        ea_perm = "CHO PHEP" if acc_info.trade_expert else "BI CHAN"
        print("\n[2] Kiem tra thong tin tai khoan MT5:")
        print(f"    - Login ID : {acc_info.login}")
        print(f"    - Ten chu TK: {acc_info.name}")
        print(f"    - Server   : {acc_info.server}")
        print(f"    - So du    : ${acc_info.balance:.2f} {acc_info.currency}")
        print(f"    - Quyen giao dich TK (account.trade_allowed): {trade_perm}")
        print(f"    - Quyen chay EA/Robot (account.trade_expert): {ea_perm}")
    else:
        print("\n[2] Khong lay duoc thong tin tai khoan MT5. Vui long dang nhap tren MT5.")

    # Check 3: Check Symbol
    print('\n[3] Kiem tra bieu tuong XAUUSD:')
    symbol = 'XAUUSD'
    sym_info = mt5.symbol_info(symbol)
    if sym_info:
        print(f'    - Symbol    : {symbol}')
        print(f'    - Gia Bid/Ask: {sym_info.bid} / {sym_info.ask}')
        print(f'    - Filling   : {sym_info.filling_mode} (1=FOK, 2=IOC)')
    else:
        print(f'    - Symbol {symbol} chua co trong Market Watch (se duoc tu dong them).')

    # Summary
    print('-' * 70)
    ready = term_info.trade_allowed and (acc_info and acc_info.trade_allowed and acc_info.trade_expert)
    if ready:
        print('>>> KET LUAN: HE THONG DA SAN SANG TU DONG GIAO DICH 100%! <<<')
    else:
        print('>>> KET LUAN: CHUA DU DIEU KIEN TU DONG VAO LENH. VUI LONG KIEM TRA LAI! <<<')
    print('=' * 70 + '\n')

    mt5.shutdown()
    return ready

if __name__ == '__main__':
    check_mt5_status()
