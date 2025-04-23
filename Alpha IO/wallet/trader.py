import os
import json
import time
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

# Environment config
PRIVATE_KEY = os.getenv("WALLET_PRIVATE_KEY")
PUBLIC_ADDRESS = os.getenv("WALLET_PUBLIC_ADDRESS")
INFURA_URL = os.getenv("WEB3_INFURA_URL")
SIMULATION = True  # Toggle for dry-run testing

# Web3 connection
web3 = Web3(Web3.HTTPProvider(INFURA_URL))
assert web3.is_connected(), "Web3 connection failed."

# Load Uniswap router ABI
with open("wallet/uniswap_router_abi.json", "r") as f:
    router_abi = json.load(f)

router_address = Web3.to_checksum_address("0xE592427A0AEce92De3Edee1F18E0157C05861564")
router = web3.eth.contract(address=router_address, abi=router_abi)

def trade_eth_to_token(token_address, eth_amount_wei):
    if SIMULATION:
        print(f"[SIMULATED TRADE] {eth_amount_wei} wei to {token_address}")
        return "simulated_tx_hash"

    deadline = int(time.time()) + 600
    txn = router.functions.exactInputSingle({
        "tokenIn": Web3.to_checksum_address("0xC02aaa39b223FE8D0a0e5C4F27eAD9083C756Cc2"),
        "tokenOut": Web3.to_checksum_address(token_address),
        "fee": 3000,
        "recipient": PUBLIC_ADDRESS,
        "deadline": deadline,
        "amountIn": eth_amount_wei,
        "amountOutMinimum": 0,
        "sqrtPriceLimitX96": 0
    }).build_transaction({
        "from": PUBLIC_ADDRESS,
        "value": eth_amount_wei,
        "gas": 300000,
        "gasPrice": web3.to_wei("30", "gwei"),
        "nonce": web3.eth.get_transaction_count(PUBLIC_ADDRESS),
    })

    signed_txn = web3.eth.account.sign_transaction(txn, private_key=PRIVATE_KEY)
    tx_hash = web3.eth.send_raw_transaction(signed_txn.rawTransaction)
    print(f"[TRADE EXECUTED] TX hash: {tx_hash.hex()}")
    return tx_hash.hex()