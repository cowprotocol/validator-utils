# WETH
NATIVE_TOKEN='0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'

ORDER_COST = {
    'amount': "1667424658616445",
    'token': NATIVE_TOKEN
}

def native_token_balance(balance):
    return {
        'amount': int(balance),
        'token': NATIVE_TOKEN,
    }

def zero_cost():
    return native_token_balance(0)
