import argparse
import asyncio
import json
import logging
import os
from copy import deepcopy
from pathlib import Path

import requests
from dotenv import load_dotenv
from duneapi.api import DuneAPI
from duneapi.types import DuneQuery, Network
from validator.amms import get_amms

from validator.common import NATIVE_TOKEN, ORDER_COST
from validator.web3 import get_block_number_from_txhash

from .util import traced

logger = logging.getLogger(__name__)

load_dotenv()


async def get_token_info_from_dune(token_addresses, external_prices):
    token_sql = ",".join(f"'\\{t[1:]}'" for t in token_addresses)
    raw_sql = f"select * from erc20.tokens where contract_address in ({token_sql})"
    query = DuneQuery.from_environment(
        raw_sql=raw_sql,
        network=Network.MAINNET,
    )
    dune_connection = DuneAPI.new_from_environment()
    data = await asyncio.to_thread(
        dune_connection.fetch,
        query,
    )
    return {
        '0' + t['contract_address'][1:]: {
            'alias': t['symbol'],
            'decimals': t['decimals'],
            'normalize_priority': 0 if t['contract_address'][1:] != NATIVE_TOKEN[1:] else 1,
            'external_price': external_prices['0' + t['contract_address'][1:]],
        } for t in data
    }

async def create_token_info(orders_info, external_prices):
    token_addresses = list({o['buyToken'] for o in orders_info} | {o['sellToken'] for o in orders_info})
 
    if NATIVE_TOKEN not in token_addresses:
        token_addresses.append(NATIVE_TOKEN)
        external_prices[NATIVE_TOKEN] = '1000000000000000000'

    return await get_token_info_from_dune(token_addresses, external_prices)

def create_order_info(order):
    return order['uid'], {
        'buy_token': order['buyToken'],
        'sell_token': order['sellToken'],
        'buy_amount': order['buyAmount'],
        'sell_amount': order['sellAmount'],
        'is_sell_order': order['kind'] == 'sell',
        'is_liquidity_order': order['isLiquidityOrder'],
        'allow_partial_fill': order['partiallyFillable'],
        'fee': {
            'amount': order['feeAmount'],
            'token': order['sellToken']
        },
        'cost': ORDER_COST, # FIXME: we don't have this info yet, assuming cow protocol orders for now
        'mandatory': False,
        'has_atomic_execution': order['isLiquidityOrder']   # FIXME: we don't have this info yet, playing safe for now
    }

def create_orders(orders_info):
    orders = {}
    for order in orders_info:
        o_id, o = create_order_info(order)
        orders[o_id] = o
    return orders

def create_metadata(gas_price, txhash):
    return {
        "gas_price": str(int(gas_price)),
        "native_token": NATIVE_TOKEN,
        "txhash": txhash 
    }

async def create_instance(orders_info, solver_competition_info):

    gas_price = solver_competition_info['gasPrice']
    return {
        'tokens': await create_token_info(orders_info, solver_competition_info['auction']['prices']),
        'orders': create_orders(orders_info),
        'amms': {}, # will be populated by lpbook amms later
        'metadata': create_metadata(gas_price, solver_competition_info['transactionHash'])
    }

def create_solution(instance, solution_info, solution_index):
    solution = {'orders': {}}
    solution['metadata'] = {
        'index': solution_index,
        'solver': solution_info['solver'],        
    }
    solution['objective'] = solution_info['objective']
    solution['prices'] = solution_info['clearingPrices']
    solution['ref_token'] = NATIVE_TOKEN
    for eo in solution_info['orders']:
        o_id = eo['id']
        o = instance['orders'][o_id]
        p_b = int(solution_info['clearingPrices'][o['buy_token']])
        p_s = int(solution_info['clearingPrices'][o['sell_token']])
        if o['is_sell_order']:
            exec_sell_amount = int(eo['executedAmount'])
            exec_buy_amount = exec_sell_amount * p_s / p_b
        else:
            exec_buy_amount = int(eo['executedAmount'])
            exec_sell_amount = exec_buy_amount * p_b / p_s
        solution['orders'][o_id] = deepcopy(instance['orders'][o_id])    
        solution['orders'][o_id]['exec_sell_amount'] = str(int(exec_sell_amount))
        solution['orders'][o_id]['exec_buy_amount'] = str(int(exec_buy_amount))
    return solution

async def fetch_instance(solver_competition_info, fetch_amms_from_lpbook):
    txhash = solver_competition_info['transactionHash']
    orderbook_url = os.getenv('ORDERBOOK_URL')
    
    orders_info = []
    for oid in solver_competition_info['auction']['orders']:
        url = orderbook_url + f'/api/v1/orders/{oid}'
        order_info = requests.get(url).json()
        orders_info.append(order_info)

    instance = await create_instance(orders_info, solver_competition_info)

    if fetch_amms_from_lpbook:
        block_number = get_block_number_from_txhash(txhash)
        amms = await get_amms(block_number, instance['tokens'], int(instance['metadata']['gas_price']))
        instance['amms'] = amms
        instance['metadata']['block_number'] = block_number

    return instance


@traced(logger, "Fetching instance and solutions.")
async def fetch_instance_and_solutions(auction_id, fetch_amms_from_lpbook):
    orderbook_url = os.getenv('ORDERBOOK_URL')
    solver_competition_url = orderbook_url + f'/api/v1/solver_competition/{auction_id}'
    solver_competition_info = requests.get(solver_competition_url).json()

    instance = await fetch_instance(solver_competition_info, fetch_amms_from_lpbook)

    solutions = []
    for solution_index, solution_info in enumerate(solver_competition_info['solutions']):
        solutions.append(create_solution(instance, solution_info, solution_index))
    
    return instance, solutions

async def main(auction_id, output_dir, fetch_amms_from_lpbook):
    instance, solutions = await fetch_instance_and_solutions(auction_id, fetch_amms_from_lpbook)
    with open(output_dir / f'instance_{auction_id}.json', 'w+') as f:
        json.dump(instance, f, indent=2)
    for solution in solutions:
        with open(
            output_dir / 
            f'solution_{auction_id}_{solution["metadata"]["index"]}_{solution["metadata"]["solver"]}.json',
            'w+'
        ) as f:
            json.dump(solution, f, indent=2)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Collect instance and solutions for a given auction."
    )

    parser.add_argument(
        'auction_id',
        type=int,
        help="Auction id."
    )

    parser.add_argument(
        'output_dir',
        type=Path,
        help="Path to directory to store instance and solution files."
    )

    parser.add_argument(
        '--use_lpbook',
        type=bool,
        default=False,
        help="Fetch amm's from lpbook."
    )

    args = parser.parse_args()

    auction_id = args.auction_id
    output_dir = args.output_dir
    fetch_amms_from_lpbook = args.use_lpbook
    asyncio.run(main(auction_id, output_dir, fetch_amms_from_lpbook))

