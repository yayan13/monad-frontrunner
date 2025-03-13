import logging
import argparse
import toml
import time
from typing import List, Dict, Any, Tuple
from decimal import Decimal
from src.settings.settings import Settings, ApiSettings, GameSettings, EOA
from src.logger.logger import Logs
from web3 import Web3

class ApiSettings:
    def __init__(self, rpc_urls: List[Dict[str, str]]):
        self.rpc_urls = rpc_urls
        self.rpc_url = rpc_urls[0]['url'] if rpc_urls else None  # Mengatur RPC default

# Konstanta
BALANCE_THRESHOLD: float = 0.001
DEFAULT_ATTEMPTS: int = 10000000
TARGET_CONTRACT: str = '0xBce2C725304e09CEf4cD7639760B67f8A0Af5bc4'
TX_COUNT: int = 100  # Jumlah transaksi yang akan dipantau
GAS_THRESHOLD: int = 300  # Ambang batas Gas Price (Gwei)
PERCENTAGE_THRESHOLD_LOW: float = 0.9  # Ambang batas persentase Gas rendah (kondisi mulai)
PERCENTAGE_THRESHOLD_HIGH: float = 0.5  # Ambang batas persentase Gas tinggi (kondisi berhenti)
PERCENTAGE_GAS_PRICE: float = 0.9  # Persentase untuk menghitung Gas Price yang akan digunakan
PERCENTAGE_GAS_LIMIT: float = 0.7  # Persentase untuk menghitung Gas Limit yang akan digunakan
ANALYSIS_INTERVAL: int = 10  # Interval analisis (detik)
RPC_SWITCH_INTERVAL: int = 3600  # Interval untuk menanyakan pergantian RPC (detik)
RPC_SELECTION_TIMEOUT: int = 3  # Waktu untuk memilih RPC (detik)

def analyze_gas_usage(w3: Web3, contract_address: str, own_address: str) -> Tuple[bool, float, int]:
    """
    Menganalisis penggunaan Gas Price dan Gas Limit pada transaksi terakhir dari alamat kontrak tertentu, 
    mengabaikan transaksi dari alamat sendiri.
    
    Args:
        w3: Objek Web3
        contract_address: Alamat kontrak yang akan dipantau
        own_address: Alamat akun sendiri, transaksi dari alamat ini akan diabaikan
        
    Returns:
        Tuple[bool, float, int]: Elemen pertama menunjukkan apakah program harus dijalankan (True=jalankan), 
        elemen kedua menunjukkan ambang batas Gas Price yang dihitung, elemen ketiga menunjukkan Gas Limit yang dihitung
    """
    logger = Logs(__name__).log(level=logging.INFO)
    logger.info(f"Memulai analisis Gas Price dan Gas Limit pada kontrak {contract_address} untuk {TX_COUNT} transaksi terakhir (mengabaikan transaksi sendiri)...")
    
    own_address = own_address.lower()
    current_block = w3.eth.block_number
    gas_prices = []
    gas_limits = []
    search_block_count = 1000
    transactions_found = 0
    
    for block_number in range(current_block, max(current_block - search_block_count, 0), -1):
        if transactions_found >= TX_COUNT:
            break
            
        try:
            block = w3.eth.get_block(block_number, full_transactions=True)
            for tx in block.transactions:
                if tx.to and tx.to.lower() == contract_address.lower():
                    if hasattr(tx, 'from') and tx['from'].lower() == own_address:
                        continue
                    gas_price_wei = tx.gasPrice
                    gas_price_gwei = w3.from_wei(gas_price_wei, 'gwei')
                    gas_prices.append(float(gas_price_gwei))
                    gas_limits.append(tx.gas)
                    transactions_found += 1
                    if transactions_found >= TX_COUNT:
                        break
        except Exception as e:
            logger.error(f"Gagal mendapatkan informasi blok {block_number}: {e}")
            continue
    
    if not gas_prices:
        logger.warning(f"Tidak ditemukan transaksi pada kontrak {contract_address} (setelah mengabaikan transaksi sendiri)")
        return True, 0, 0
    
    gas_prices.sort()
    gas_limits.sort()
    low_percentile_index = int(len(gas_prices) * PERCENTAGE_THRESHOLD_LOW)
    low_percentile_gas_price = gas_prices[low_percentile_index]
    high_percentile_index = int(len(gas_prices) * (1 - PERCENTAGE_THRESHOLD_HIGH))
    high_percentile_gas_price = gas_prices[high_percentile_index]
    gas_price_index = int(len(gas_prices) * PERCENTAGE_GAS_PRICE)
    tx_gas_price = gas_prices[gas_price_index] + 50
    
    gas_limit_index = int(len(gas_limits) * PERCENTAGE_GAS_LIMIT)
    tx_gas_limit = gas_limits[gas_limit_index]
    
    logger.info(f"Menemukan {len(gas_prices)} transaksi (setelah mengabaikan transaksi sendiri)")
    logger.info(f"{PERCENTAGE_THRESHOLD_LOW*100}% transaksi memiliki Gas Price {low_percentile_gas_price} Gwei (kondisi mulai)")
    logger.info(f"{PERCENTAGE_THRESHOLD_HIGH*100}% transaksi memiliki Gas Price {high_percentile_gas_price} Gwei (kondisi berhenti)")
    logger.info(f"Menggunakan Gas Price {tx_gas_price} Gwei (berdasarkan {PERCENTAGE_GAS_PRICE*100}% transaksi) untuk mengirim transaksi")
    logger.info(f"Menggunakan Gas Limit {tx_gas_limit} (berdasarkan {PERCENTAGE_GAS_LIMIT*100}% transaksi) untuk mengirim transaksi")
    
    should_stop = high_percentile_gas_price > GAS_THRESHOLD
    should_start = (not should_stop) and (low_percentile_gas_price < GAS_THRESHOLD)
    
    if tx_gas_limit > 150000:
        logger.warning(f"Gas Limit {tx_gas_limit} melebihi batas 150000, menghentikan program")
        return False, tx_gas_price, tx_gas_limit
    
    if should_stop:
        logger.info(f"{PERCENTAGE_THRESHOLD_HIGH*100}% transaksi memiliki Gas Price di atas {GAS_THRESHOLD} Gwei, menghentikan program")
        return False, tx_gas_price, tx_gas_limit
    elif should_start:
        logger.info(f"{PERCENTAGE_THRESHOLD_LOW*100}% transaksi memiliki Gas Price di bawah {GAS_THRESHOLD} Gwei, memulai program")
        return True, tx_gas_price, tx_gas_limit
    else:
        logger.info("Kondisi mulai tidak terpenuhi, melanjutkan pemantauan...")
        return False, tx_gas_price, tx_gas_limit

def select_rpc(rpc_list: List[Dict[str, str]]) -> str:
    """
    Menampilkan daftar RPC yang tersedia dan meminta pengguna untuk memilih.
    
    Args:
        rpc_list: Daftar RPC yang tersedia
        
    Returns:
        str: URL RPC yang dipilih
    """
    print("\nPilih RPC yang akan digunakan:")
    for i, rpc in enumerate(rpc_list):
        print(f"{i + 1}. {rpc['name']} - {rpc['url']}")
    
    start_time = time.time()  # Waktu mulai pemilihan RPC
    timeout = RPC_SELECTION_TIMEOUT  # Durasi timeout

    while True:
        try:
            choice = input(f"Masukkan nomor RPC (1-{len(rpc_list)}), atau tekan Enter untuk menggunakan RPC terakhir: ")
            if choice == "":
                return None  # Kembali ke RPC terakhir jika tidak ada input
            choice = int(choice)
            if 1 <= choice <= len(rpc_list):
                return rpc_list[choice - 1]['url']
        except ValueError:
            pass

        # Cek apakah waktu telah melebihi timeout
        if time.time() - start_time > timeout:
            print(f"Waktu pemilihan RPC telah habis setelah {timeout} detik. Menggunakan RPC terakhir.")
            return None

def play() -> None:
    parser = argparse.ArgumentParser(description="Break Monad Frontrunner Bot.")
    parser.add_argument('--gas_price_gwei', type=int, default=0, help="Set the gas price in GWEI.")
    parser.add_argument('--attempts', type=int, default=False, help="Number of attempts to play.")
    parser.add_argument('--interval', type=float, default=1, help="Delay between attempts in seconds.")
    parser.add_argument('--skip_gas_check', action='store_true', help="Skip gas usage analysis.")
    parser.add_argument('--analysis_interval', type=int, default=ANALYSIS_INTERVAL, help="Interval between gas analyses in seconds.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    logger = Logs(__name__).log(level=logging.INFO)

    # 1. Load config
    config_file = toml.load('settings.toml')

    # 2. Parse config
    settings = Settings(
        api_settings=ApiSettings(rpc_urls=config_file['api_settings']['rpc_urls']),
        game_settings=GameSettings(**config_file['game_settings']),
        eoa=EOA(**config_file['eoa'])
    )

    rpc_list = config_file['api_settings']['rpc_urls']
    current_rpc_index = 0
    w3 = Web3(Web3.HTTPProvider(rpc_list[current_rpc_index]['url']))

    if not w3.is_connected():
        raise Exception("Gagal terhubung ke jaringan Ethereum.")
    else:
        logger.info(f"Terhubung ke jaringan Monad menggunakan RPC: {rpc_list[current_rpc_index]['name']}")

    account = w3.eth.account.from_key(settings.eoa.private_key)
    logger.info(f"Akun yang digunakan: {account.address}")

    balance = w3.from_wei(w3.eth.get_balance(account.address), 'ether')
    logger.info(f"Saldo akun: {balance} Testnet Monad")

    if balance < BALANCE_THRESHOLD:
        logger.error("Saldo akun terlalu rendah untuk bermain. Silakan tambahkan dana ke akun.")
        logger.warning("Keluar...")
        time.sleep(1)
        return

    contract = w3.eth.contract(
        address=w3.to_checksum_address(settings.game_settings.frontrunner_contract_address),
        abi=settings.game_settings.abi
    )

    try:
        wins, losses = contract.functions.getScore(account.address).call()
        if wins > 0 or losses > 0:
            logger.info(f"Sepertinya ini bukan pertama kalinya: Anda menang {wins} kali dan kalah {losses} kali.")
        else:
            logger.info("Sepertinya ini pertama kalinya Anda bermain. Semoga beruntung!")
    except Exception as e:
        logger.error(f"Gagal mendapatkan skor: {e} - Melewati...")

    chain_id: int = w3.eth.chain_id
    attempts = DEFAULT_ATTEMPTS if args.attempts == False else args.attempts

    last_analysis_time = 0
    is_running = False
    next_analysis_time = 0
    gas_price_value = 0
    gas_limit_value = 0
    analysis_in_progress = False
    last_rpc_switch_time = time.time()

    if not args.skip_gas_check:
        should_run, gas_price_value, gas_limit_value = analyze_gas_usage(w3, TARGET_CONTRACT, account.address)
        last_analysis_time = time.time()
        next_analysis_time = last_analysis_time + args.analysis_interval
        is_running = should_run

    while True:
        current_time = time.time()

        if (current_time >= next_analysis_time and not args.skip_gas_check) and not analysis_in_progress:
            analysis_in_progress = True
            should_run, new_gas_price_value, new_gas_limit_value = analyze_gas_usage(w3, TARGET_CONTRACT, account.address)
            if is_running and not should_run:
                logger.warning("Gas Price meningkat, akan berhenti setelah transaksi saat ini selesai.")
                is_running = False
            elif not is_running and should_run:
                logger.info("Gas Price sesuai, memulai/melanjutkan transaksi.")
                is_running = True
            gas_price_value = new_gas_price_value
            gas_limit_value = new_gas_limit_value
            last_analysis_time = time.time()
            next_analysis_time = last_analysis_time + args.analysis_interval
            analysis_in_progress = False

        if args.skip_gas_check:
            is_running = True
            gas_price_value = args.gas_price_gwei if args.gas_price_gwei > 0 else int(w3.eth.gas_price*10**-9)
            gas_limit_value = 90000  # Default Gas Limit jika skip gas check

        if is_running:
            gas_price_gwei = args.gas_price_gwei if args.gas_price_gwei > 0 else (int(gas_price_value) if gas_price_value > 0 else int(w3.eth.gas_price*10**-9))
            gas_price_wei = w3.to_wei(gas_price_gwei, 'gwei')
            logger.info(f"Menggunakan Gas Price: {gas_price_gwei} GWEI dan Gas Limit: {gas_limit_value}")
            nonce = w3.eth.get_transaction_count(account.address)
            txs_sent = 0

            while time.time() < next_analysis_time and (attempts == DEFAULT_ATTEMPTS or txs_sent < attempts):
                try:
                    txn = contract.functions.frontrun().build_transaction({
                        'chainId': chain_id,
                        'gas': gas_limit_value,
                        'gasPrice': gas_price_wei,
                        'nonce': nonce,
                    })
                    signed_txn = account.sign_transaction(txn)
                    tx_hash = w3.eth.send_raw_transaction(signed_txn.raw_transaction)
                    logger.info(f"Mengirim transaksi, nonce: {nonce}, Tx hash: {tx_hash.hex()}")

                    # Mendapatkan skor terbaru setelah transaksi
                    wins, losses = contract.functions.getScore(account.address).call()
                    logger.info(f"Skor terbaru: Menang {wins} kali, Kalah {losses} kali")
                except Exception as e:
                    logger.error(f"Gagal mengirim transaksi, nonce: {nonce}, error: {e}")
                nonce += 1
                txs_sent += 1
                if txs_sent >= attempts and attempts != DEFAULT_ATTEMPTS:
                    logger.info("Mencapai batas percobaan, program berakhir...")
                    return
                time.sleep(args.interval)
                if not args.skip_gas_check and time.time() >= next_analysis_time:
                    logger.info("Mencapai interval analisis, mempersiapkan analisis ulang...")
                    break
            if attempts != DEFAULT_ATTEMPTS and txs_sent >= attempts:
                logger.info("Menyelesaikan semua percobaan, program berakhir...")
                return

        if not is_running:
            time.sleep(1)

        if current_time - last_rpc_switch_time >= RPC_SWITCH_INTERVAL:
            selected_rpc_url = select_rpc(rpc_list)
            if selected_rpc_url:
                w3 = Web3(Web3.HTTPProvider(selected_rpc_url))
                if not w3.is_connected():
                    logger.error("Gagal terhubung ke RPC yang dipilih. Menggunakan RPC sebelumnya.")
                else:
                    logger.info(f"Berhasil beralih ke RPC: {selected_rpc_url}")
            last_rpc_switch_time = time.time()

if __name__ == "__main__":
    play()