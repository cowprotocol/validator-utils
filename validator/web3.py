
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from web3 import Web3
import logging
from .util import traced
import warnings

load_dotenv()

logger = logging.getLogger(__name__)

w3 = Web3(Web3.HTTPProvider(os.getenv('WEB3_URL')))

def get_lp_uni_v2_swaps(txhash, w3):
    # uni v2
    with open(Path(__file__).parent /'./artifacts/uniswap_v2_pair.abi', 'r') as f:
        contract_abi = json.load(f)

    UniV2 = w3.eth.contract(abi=contract_abi)

    receipt = w3.eth.getTransactionReceipt(txhash)
    swaps = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        swap_events = UniV2.events.Swap().processReceipt(receipt)

    for swap_event in swap_events:
        address = swap_event['address']

        token0 = UniV2(address).functions.token0().call().lower()
        token1 = UniV2(address).functions.token1().call().lower()

        if address not in swaps.keys():
            swaps[address.lower()] = []
        if swap_event['args']['amount0In'] > 0:
            swaps[address.lower()].append({
                'buy_token': token0,
                'sell_token': token1,
                'exec_buy_amount': int(swap_event['args']['amount0In']),
                'exec_sell_amount': int(swap_event['args']['amount1Out']),
            })
        else:
            swaps[address.lower()].append({
                'buy_token': token1,
                'sell_token': token0,
                'exec_buy_amount': int(swap_event['args']['amount1In']),
                'exec_sell_amount': int(swap_event['args']['amount0Out']),
            })
    return swaps

@traced(logger, 'Getting public pool swaps through web3.')
def get_lp_swaps(txhash):
    # uni v2
    swaps_univ2 = get_lp_uni_v2_swaps(txhash, w3)
    return swaps_univ2

@traced(logger, 'Getting block number from txhash through web3.')
def get_block_number_from_txhash(txhash):
    return w3.eth.get_transaction(txhash)['blockNumber']
