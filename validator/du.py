import argparse
import asyncio
from inspect import trace
import json
import logging
import os
from copy import deepcopy
from math import ceil

import requests
from dotenv import load_dotenv
from prettytable import PrettyTable

from validator.amms import get_amms
from validator.common import NATIVE_TOKEN, zero_cost
from validator.util import traced_context
from validator.web3 import get_lp_swaps

from .dune import get_block_number_from_txhash
from .instance_collect import fetch_instance_and_solutions

logger = logging.getLogger(__name__)

load_dotenv()


def create_updated_order(order_id, instance, solution):
    o = instance['orders'][order_id]
    updated_o = deepcopy(o) 

    # If order is in the solution, then add order with limit price = executed price
    # otherwise add order with original limit price.
    if order_id in solution['orders']:
        eo = solution['orders'][order_id]

        # if order is in solution, then filling it a bit more comes for free
        updated_o['cost'] = zero_cost()

        if o['is_sell_order']:
            max_sell_amount = int(o['sell_amount'])
            exec_buy_amount = int(eo['exec_buy_amount'])
            exec_sell_amount = int(eo['exec_sell_amount'])
            new_buy_amount = max_sell_amount * exec_buy_amount / exec_sell_amount
            updated_o['buy_amount'] = str(int(new_buy_amount))
        else:
            max_buy_amount = int(o['buy_amount'])
            exec_buy_amount = int(eo['exec_buy_amount'])
            exec_sell_amount = int(eo['exec_sell_amount'])
            new_sell_amount = max_buy_amount * exec_sell_amount / exec_buy_amount
            updated_o['sell_amount'] = str(int(new_sell_amount))

        updated_o['allow_partial_fill'] = True
        updated_o['is_mandatory'] = True

    return updated_o      


def update_amm_reserves_from_execution(amm, execution):
    if amm['protocol'] in ['Uniswap_2', 'Sushiswap_2', 'Curve_']:
        for token_i in range(len(amm['tokens'])):
            token = amm['tokens'][token_i]
            for swap in execution:
                if swap['sell_token'] == token['address']:
                    amm['state']['balances'][token_i] = str(
                        int(amm['state']['balances'][token_i]) - swap['exec_sell_amount']
                    )
                elif swap['buy_token'] == token['address']:
                    amm['state']['balances'][token_i] = str(
                        int(amm['state']['balances'][token_i]) + swap['exec_buy_amount']
                    )


async def create_updated_instance(instance, solution):
    orders = {o_id: create_updated_order(o_id, instance, solution) for o_id in instance['orders'].keys()}

    amms = instance['amms']
    txhash = instance['metadata']['txhash']
    lpswaps = get_lp_swaps(txhash)

    for amm_id, execution in lpswaps.items():
        if amm_id in amms.keys():
            amms[amm_id]['cost'] = zero_cost()  # amm was already used so cost is zero
            update_amm_reserves_from_execution(amms[amm_id], execution)

    tokens = instance['tokens']
    
    return {
        'tokens': tokens,
        'orders': orders,
        'amms': amms,
        'metadata': instance['metadata']
    }


def create_metadata(gas_price_wei):
    return {
        "gas_price": str(gas_price_wei),
        "native_token": NATIVE_TOKEN
    }

async def solve_single_order(single_order_instance):
    quasimodo_url = os.getenv('QUASIMODO_URL')
    params = {
        'use_lpbook': False,
        'objective': 'SurplusFeesCosts',
        'ucp_policy': 'Ignore',
        'use_internal_buffers': False, # TODO: also interesting what to do here,
        'time_limit': 10, 
    }
    response = await asyncio.to_thread(
        requests.post,
        quasimodo_url + '/solve',
        params=params,
        json=single_order_instance
    )
    if response.status_code != 200:
        logging.error(f"Error solving single order instance. Solver replied with {response.status_code}: {response.text}")
        raise RuntimeError("Error solving single order instance")

    return response.json()


def compute_order_surplus(o_id, original_instance, solution):
    if o_id not in solution['orders'].keys():
        exec_buy_amount, exec_sell_amount = 0, 0
    else:
        eo = solution['orders'][o_id]
        exec_buy_amount, exec_sell_amount = int(eo['exec_buy_amount']), int(eo['exec_sell_amount'])

    o = original_instance['orders'][o_id]
    if o['is_sell_order']:
        token = o['buy_token']
        surplus = exec_buy_amount - exec_sell_amount * int(o['buy_amount']) \
            / int(o['sell_amount'])
    else:
        token = o['sell_token']
        surplus = exec_buy_amount * int(o['exec_sell_amount']) \
            / int(o['exec_buy_amount']) - exec_sell_amount

    return token, surplus


def compute_order_disregarded_utility(o_id, updated_instance, submitted_solution, solution):
    def compute_exec_amounts_and_xrate(solution):
        if o_id not in solution['orders']:
            exec_buy_amount, exec_sell_amount = 0, 0
            xrate = None
        else:
            eo = solution['orders'][o_id]
            exec_buy_amount, exec_sell_amount = int(eo['exec_buy_amount']), int(eo['exec_sell_amount'])
            xrate = exec_sell_amount / exec_buy_amount
        return exec_sell_amount, exec_buy_amount, xrate

    sell_amount_s, buy_amount_s, xrate_s = compute_exec_amounts_and_xrate(submitted_solution)
    sell_amount_f, buy_amount_f, xrate_f = compute_exec_amounts_and_xrate(solution)

    updated_o = updated_instance['orders'][o_id]
    if updated_o['is_sell_order']:
        sell_amount_at_xrate_s = max(0, sell_amount_s - sell_amount_f)
        buy_amount_at_xrate_s = sell_amount_at_xrate_s / xrate_s if sell_amount_at_xrate_s != 0 else 0
        buy_amount_at_xrate_f = sell_amount_f / xrate_f if sell_amount_f != 0 else 0
        du = buy_amount_at_xrate_s + buy_amount_at_xrate_f - buy_amount_s
        token = updated_o['buy_token']
    else:
        buy_amount_at_xrate_s = max(0, buy_amount_s - buy_amount_f)
        sell_amount_at_xrate_s = buy_amount_at_xrate_s * xrate_s if buy_amount_at_xrate_s != 0 else 0
        sell_amount_at_xrate_f = buy_amount_f * xrate_f if buy_amount_f != 0 else 0
        du = sell_amount_s - (sell_amount_at_xrate_s + sell_amount_at_xrate_f)
        token = updated_o['sell_token']

    return token, du


def compute_order_disregarded_utility_info(o_id, original_instance, updated_instance, submitted_solution, solution):
    token, du = compute_order_disregarded_utility(o_id, updated_instance, submitted_solution, solution)
    _, surplus = compute_order_surplus(o_id, original_instance, submitted_solution)

    token_decimals = updated_instance['tokens'][token]['decimals']
    native_decimals = updated_instance['tokens'][NATIVE_TOKEN]['decimals']
    du_dec = du / 10**token_decimals
    surplus_dec = surplus / 10**token_decimals

    # (amount_ref / amount_tk) = p_tk / p_ref * 10^(d_tk - d_ref)
    token_external_price = int(updated_instance['tokens'][token]['external_price'])
    native_external_price = int(updated_instance['tokens'][NATIVE_TOKEN]['external_price'])
    token_external_price_dec = token_external_price / native_external_price * 10**(token_decimals - native_decimals)
    du_eth = du_dec * token_external_price_dec
    surplus_eth = surplus_dec * token_external_price_dec

    if o_id not in submitted_solution['orders']:
        du_perc = du * float('inf')
        surplus_perc = 0
    else:
        o = submitted_solution['orders'][o_id]
        du_perc = 100 * du / int(submitted_solution['orders'][o_id]['exec_buy_amount'])
        surplus_perc = 100 * surplus / int(submitted_solution['orders'][o_id]['exec_buy_amount'])

    return {
        'token': token,
        'du': du,
        'du_dec': du_dec,
        'du_ETH': du_eth,
        'du_perc': du_perc,
        'surplus': surplus,
        'surplus_dec': surplus_dec,
        'surplus_ETH': surplus_eth,
        'surplus_perc': surplus_perc,
    }


async def compute_disregarded_utility_info(original_instance, updated_instance, original_solution, settled_orders_only):
    du = []
    liquidity_orders = {o_id: o for o_id, o in updated_instance['orders'].items() if o['is_liquidity_order']}
    async def compute_du(o_id): 
        if settled_orders_only and o_id not in original_solution['orders'].keys():
            return
        updated_o = updated_instance['orders'][o_id]
        if not updated_o['is_liquidity_order']:
            single_order_instance = {
                'tokens' : updated_instance['tokens'],
                'amms': updated_instance['amms'],
                'orders': {o_id: updated_o, **liquidity_orders},
                'metadata': updated_instance['metadata'],            
            }

            solution = await solve_single_order(single_order_instance)
            du_for_order = compute_order_disregarded_utility_info(o_id, original_instance, updated_instance, original_solution, solution)
            du_for_order['order_id'] = o_id
            du.append(du_for_order)


    nr_instances = len(updated_instance['orders'].keys())
    with traced_context(logger, f"Solving {nr_instances} single order instances with Quasimodo ..."):
        await asyncio.gather(*[compute_du(o_id) for o_id in updated_instance['orders'].keys()])

    return du


def shorten_address(address, length=16):
    prefix_len = ceil((length -2)/2)
    suffix_len = (length-2)//2
    return address[:prefix_len]+".."+address[-suffix_len:]


def print_disregarded_utility(solution, solution_dus, instance):

    solution_dus = sorted(solution_dus, key=lambda d: (-d['du_ETH'], d['order_id']))

    def get_row(du):
        token = instance["tokens"][du["token"]]["alias"]
        du_dec = f'{du["du_dec"]:.4f} {token}'
        du_eth = f'{du["du_ETH"]:.6f}'
        surplus_dec = f'{du["surplus_dec"]:.4f} {token}'
        surplus_eth = f'{du["surplus_ETH"]:.6f}'
        return [
            shorten_address(du["order_id"]),
            surplus_dec,
            surplus_eth,
            f'{du["surplus_perc"]:.4f}',
            du_dec,
            du_eth,
            f'{du["du_perc"]:.4f}'
        ]

    table = [get_row(du) for du in solution_dus]
    tab = PrettyTable(['Order', 'Surplus', 'Surplus (ETH)', 'Surplus (%)', 'DU', 'DU (ETH)', 'DU (%)'])
    tab.add_rows(table)

    solver = solution['metadata']['solver']
    txhash = instance['metadata']['txhash']
    block_number = instance['metadata']['block_number']
    print(f'Solver            :\t{solver}')
    print(f'Solution (txhash) :\t{txhash}')
    print(f'Solution (block)  :\t{block_number}')
    print(tab)


async def compute_auction_disregarded_utility_info(auction_id, settled_orders_only):
    instance, solutions = await fetch_instance_and_solutions(auction_id, fetch_amms_from_lpbook=True)
    winning_solution = solutions[-1]
    updated_instance = await create_updated_instance(instance, winning_solution)
    du = await compute_disregarded_utility_info(instance, updated_instance, winning_solution, settled_orders_only)
    return du, instance, winning_solution


async def main(auction_id, settled_orders_only):
    du, instance, winning_solution = await compute_auction_disregarded_utility_info(auction_id, settled_orders_only)
    print_disregarded_utility(winning_solution, du, instance)

if __name__ == '__main__':

    parser = argparse.ArgumentParser(
        description="Compute disregarded utility of the winning solution of a given settlement."
    )

    parser.add_argument(
        'auction_id',
        type=int,
        help="Auction id."
    )

    parser.add_argument(
        '--settled_orders_only',
        type=bool,
        help="Compute disregarded utility only for orders that were settled in the solution.",
        default=True
    )

    
    args = parser.parse_args()

    auction_id = args.auction_id
    settled_orders_only = args.settled_orders_only

    logging.config.fileConfig(fname='logging.conf', disable_existing_loggers=True)

    asyncio.run(main(auction_id, settled_orders_only))
