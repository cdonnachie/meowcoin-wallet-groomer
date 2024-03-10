#!/usr/bin/python3
# simple cleanup script, 2012-12-25 <greg@xiph.org>
# 2018: updated by brianmct
# 2022: updated by cdonnachie
import sys
import operator
from decimal import Decimal
from bitcoinrpc.authproxy import AuthServiceProxy
import argparse

parser = argparse.ArgumentParser(description='This script generates transaction(s) to cleanup your wallet.\n'
'It looks for the single addresses which have the most small confirmed payments made to them and merges\n'
'all those payments, along with those for any addresses which are all tiny payments, to a single txout.\n'
'It must connect to meowcoin to inspect your wallet and to get fresh addresses to pay your coin to.')
parser.add_argument('rpc_server', type=str, help='Wallet RPC server info. '
                    'Example: http://user:password@127.0.0.1:9766')
parser.add_argument('-i', '--max_amt_input', type=Decimal, default=Decimal('25'),
  help='The maximum input amount of a single transaction to consolidate (default: 25 MEWC)')
parser.add_argument('-n', '--max_num_tx', type=int, default=500,
  help='The maximum number of transactions to consolidate at once. Lower this if you are getting a tx-size error (default: 500)')
parser.add_argument('-o', '--max_amt_per_output', type=Decimal, default=Decimal('10000'),
  help='The maximum amount (in MEWC) to send to a single output address (default: 10000 MEWC)')
parser.add_argument('-f', '--fee', type=Decimal, default=Decimal('1'),
  help='The amount of fees (in MEWC) to use for the transaction')

args = parser.parse_args()

try:
  b = AuthServiceProxy(args.rpc_server)
  b.getblockchaininfo()
except Exception as e:
  print("\033[91mCouldn't connect to meowcoin:", "\033[0m", e)
  sys.exit()

walletinfo = b.getwalletinfo()
try:
    walletEncrypted = walletinfo.get('unlocked_until', None)
    if walletEncrypted == 0:
      print("\033[91mWallet is locked. Please unlock it in the Core wallet debug console.", "\033[0m")    
      print("Run to unlock for 5 minutes: \033[33mwalletpassphrase\033[0m \033[36myour_password\033[0m \033[33m600 ", "\033[0m")    
      sys.exit()
    elif walletEncrypted is None:
      print("\033[33mWallet is not encrypted; you should consider encrypting it as soon as possible!", "\033[0m")    
    else:
      print("\033[32mWallet is unlocked.", "\033[0m")
except Exception as e:
    print("Error occurred:", e)

# Loop until wallet is clean
while True:
  try:
    coins = b.listunspent(1, 99999999)
  except Exception as e:
    print("\033[91mError occurred while fetching unspent transactions:", e, "\033[0m")    
    sys.exit()

  scripts = {}
  for coin in coins:
    script = coin['scriptPubKey']
    if script not in scripts:
      scripts[script] = (0, Decimal('0'), 0)
    if (coin['amount'] < args.max_amt_input and coin['amount'] >= Decimal('0.01') and coin['confirmations'] > 100):
      scripts[script] = (scripts[script][0] + 1, scripts[script][1] + coin['amount'], scripts[script][0] + 1)
    else:
      scripts[script] = (scripts[script][0], scripts[script][1] + coin['amount'], scripts[script][0] + 1)

  if len(scripts) == 0:
    print("\033[32mWallet already clean.", "\033[0m")    
    sys.exit()

  # Which script has the largest number of well confirmed small but not dust outputs?
  most_overused = max(scripts.items(), key=operator.itemgetter(1))[0]

  # If the best we can do doesn't reduce the number of txouts or just moves dust, give up.
  if scripts[most_overused][2] < 3 or scripts[most_overused][1] < Decimal('0.01'):
    print("\033[32mWallet already clean.", "\033[0m")    
    sys.exit()

  usescripts = set([most_overused])

  # Also merge in scripts that are all dust, since they can't be spent without merging with something.
  for script in scripts.keys():
    if scripts[script][1] < Decimal('0.00010000'):
      usescripts.add(script)

  amt = Decimal('0')
  txouts = []
  for coin in coins:
    if len(txouts) >= args.max_num_tx:
      break
    if coin['scriptPubKey'] in usescripts:
      amt += coin['amount']
      txout = {}
      txout['txid'] = coin['txid']
      txout['vout'] = coin['vout']
      txouts.append(txout)
  print('Creating tx from %d inputs of total value %s:' % (len(txouts), amt))
  for script in usescripts:
    print('  Script %s has %d txins and %s MEWC value.' % (script, scripts[script][2], str(scripts[script][1])))

  out = {}
  na = amt - args.fee
  # One new output per max_amt_per_output MEWC of value to avoid consolidating too much value in too few addresses.
  # But don't add an extra output if it would have less than args.max_amt_per_output MEWC.
  while na > 0:
    amount = min(args.max_amt_per_output, na)
    if (na - amount) < Decimal('10'):
      amount = na
    addr = b.getnewaddress('consolidate')
    if amount > 0:
      if addr not in out:
        out[addr] = Decimal('0')
      out[addr] += amount
    na -= amount
  print('Paying %s MEWC (%s fee) to:' % (sum(out.values()), amt - sum(out.values())))
  for o in out.keys():
    print('  %s %s' % (o, out[o]))

  try:
    txn = b.createrawtransaction(txouts, out)
    a = input('Sign the transaction? [y]/n: ')
    if a == 'n' or a == 'N':
      sys.exit()

    signed_txn = b.signrawtransaction(txn)
    print('Bytes: %d Fee: %s' % (len(signed_txn['hex']) / 2, amt - sum(out.values())))

    a = input('Send the transaction? [y]/n: ')
    if a == 'n' or a == 'N':
      sys.exit()

    txid = b.sendrawtransaction(signed_txn['hex'])
    print('Transaction sent! txid: %s\n' % txid)
  except Exception as e:
    print("\033[91mError occurred during transaction creation/sending:", e, "\033[0m")    
    sys.exit()