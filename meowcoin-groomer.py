#!/usr/bin/env python3
"""
Wallet Cleanup Script
Original: 2012-12-25 (greg@xiph.org)
Updates: 2018 (brianmct), 2024 (cdonnachie)
"""

import sys
import argparse
import operator
from decimal import Decimal
from bitcoinrpc.authproxy import AuthServiceProxy


def parse_arguments():
    parser = argparse.ArgumentParser(
        description=(
            "Generates transaction(s) to clean up your Meowcoin wallet.\n"
            "Finds addresses with many small confirmed payments and merges them into larger outputs."
        )
    )
    parser.add_argument("rpc_server", help="Wallet RPC server URI (e.g., http://user:password@127.0.0.1:9766)")
    parser.add_argument("-i", "--max_amt_input", type=Decimal, default=Decimal("25"))
    parser.add_argument("-n", "--max_num_tx", type=int, default=500)
    parser.add_argument("-o", "--max_amt_per_output", type=Decimal, default=Decimal("10000"))
    parser.add_argument("-f", "--fee", type=Decimal, default=Decimal("1"))
    parser.add_argument("--reuse", action="store_true")
    parser.add_argument("-a", "--address", type=str, default=None)
    parser.add_argument("--auto", action="store_true")
    return parser.parse_args()


def connect_wallet(rpc_server):
    try:
        rpc = AuthServiceProxy(rpc_server)
        rpc.getblockchaininfo()
        return rpc
    except Exception as e:
        print(f"\033[91mCouldn't connect to meowcoin:\033[0m {e}")
        sys.exit()


def validate_address(rpc, address):
    info = rpc.validateaddress(address)
    if not info.get("isvalid"):
        print(f"\033[91mInvalid address:\033[0m {address}")
        sys.exit()
    if not info.get("ismine"):
        print(f"\033[91mAddress is not in the wallet:\033[0m {address}")
        sys.exit()


def check_wallet_encryption(rpc):
    walletinfo = rpc.getwalletinfo()
    status = walletinfo.get("unlocked_until")
    if status == 0:
        print("\033[91mWallet is locked. Please unlock it.\033[0m")
        print("Example: \033[33mwalletpassphrase\033[0m \033[36myour_password\033[0m \033[33m600\033[0m")
        sys.exit()
    elif status is None:
        print("\033[33mWallet is not encrypted; consider encrypting it.\033[0m")
    else:
        print("\033[32mWallet is unlocked.\033[0m")


def get_consolidatable_scripts(rpc, max_input_amt):
    try:
        coins = rpc.listunspent(1, 99999999)
    except Exception as e:
        print(f"\033[91mError fetching unspent transactions:\033[0m {e}")
        sys.exit()

    scripts = {}
    for coin in coins:
        script = coin["scriptPubKey"]
        amt = coin["amount"]
        conf = coin["confirmations"]
        small_confirmed = amt < max_input_amt and amt >= Decimal("0.01") and conf > 100

        count, total, total_count = scripts.get(script, (0, Decimal("0"), 0))
        scripts[script] = (
            count + 1 if small_confirmed else count,
            total + amt,
            total_count + 1
        )

    return scripts, coins


def build_transaction(rpc, args, coins, scripts):
    most_used = max(scripts.items(), key=operator.itemgetter(1))[0]
    txin_count, txin_total, total = scripts[most_used]

    if total < 3 or txin_total < Decimal("0.01"):
        return None, None

    use_scripts = {most_used}
    use_scripts.update(k for k, v in scripts.items() if v[1] < Decimal("0.0001"))

    amt, txins = Decimal("0"), []
    for coin in coins:
        if len(txins) >= args.max_num_tx:
            break
        if coin["scriptPubKey"] in use_scripts:
            txins.append({"txid": coin["txid"], "vout": coin["vout"]})
            amt += coin["amount"]

    remaining = amt - args.fee
    out = {}
    addr = args.address

    while remaining > 0:
        send_amt = min(args.max_amt_per_output, remaining)
        if (remaining - send_amt) < Decimal("10"):
            send_amt = remaining

        if not addr:
            if not args.reuse or not out:
                addr = rpc.getnewaddress("consolidate")
        out[addr] = out.get(addr, Decimal("0")) + send_amt
        remaining -= send_amt

    return txins, out


def confirm_and_send(rpc, args, txins, out):
    try:
        txn = rpc.createrawtransaction(txins, out)
        if not args.auto and input("Sign the transaction? [y]/n: ").lower() == "n":
            sys.exit()

        signed = rpc.signrawtransaction(txn)
        print(f"Bytes: {len(signed['hex']) / 2:.0f} Fee: {sum(out.values()) - sum(c['amount'] for c in txins)}")

        if not args.auto and input("Send the transaction? [y]/n: ").lower() == "n":
            sys.exit()

        txid = rpc.sendrawtransaction(signed["hex"])
        print(f"\033[32mTransaction sent! txid: {txid}\033[0m")
        return txid
    except Exception as e:
        print(f"\033[91mTransaction error:\033[0m {e}")
        sys.exit()


def main():
    args = parse_arguments()
    rpc = connect_wallet(args.rpc_server)

    if args.address:
        validate_address(rpc, args.address)

    check_wallet_encryption(rpc)
    transactions = []

    while True:
        scripts, coins = get_consolidatable_scripts(rpc, args.max_amt_input)
        txins, out = build_transaction(rpc, args, coins, scripts)

        if not txins:
            if transactions:
                print("\033[32mAll cleanup transactions sent:\033[0m", transactions)
            else:
                print("\033[32mWallet already clean.\033[0m")
            break

        print(f"Creating transaction from {len(txins)} inputs...")
        txid = confirm_and_send(rpc, args, txins, out)
        transactions.append(txid)


if __name__ == "__main__":
    main()
