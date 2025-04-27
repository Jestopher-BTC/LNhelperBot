import os
import requests
import matplotlib.pyplot as plt
import numpy as np
from dotenv import load_dotenv
import concurrent.futures
import time

load_dotenv()

AMBOSS_API_URL = "https://api.amboss.space/graphql"

def get_btc_usd():
    """Fetch the current BTC/USD price from CoinGecko."""
    resp = requests.get('https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd')
    resp.raise_for_status()
    return resp.json()['bitcoin']['usd']

def get_enabled_offer_ids():
    api_key = os.environ.get("AMBOSS_API_KEY")  # Loaded from .env
    headers = {"x-api-key": api_key}
    query = """
    query {
      getOffers {
        list {
          id
          status
        }
      }
    }
    """
    resp = requests.post(AMBOSS_API_URL, json={'query': query}, headers=headers)
    resp.raise_for_status()
    offers = resp.json()['data']['getOffers']['list']
    return [offer['id'] for offer in offers if offer['status'] == "ENABLED"]

def get_offer_details(offer_id):
    api_key = os.environ.get("AMBOSS_API_KEY")  # Loaded from .env
    headers = {"x-api-key": api_key}
    query = f"""
    query {{
      getOffer(id: "{offer_id}") {{
        base_fee
        fee_rate
        amboss_fee_rate
        min_size
        max_size
        account
        id
        conditions {{
          condition
          operator
          value
        }}
      }}
    }}
    """
    try:
        resp = requests.post(AMBOSS_API_URL, json={'query': query}, headers=headers)
        print("Raw response:", resp.text)
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            print(f"Offer {offer_id} error: {data['errors']}")
            return None
        return data['data']['getOffer']
    except Exception as e:
        print(f"Offer {offer_id} generated an exception: {e}")
        return None

def usd_to_sats(usd_amount, btc_usd_price):
    btc = usd_amount / btc_usd_price
    sats = btc * 100_000_000
    return int(sats)

def sats_to_usd(sats, btc_usd_price):
    return sats * btc_usd_price / 100_000_000

def get_all_offer_details(offer_ids):
    offers = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_to_id = {executor.submit(get_offer_details, oid): oid for oid in offer_ids}
        for i, future in enumerate(concurrent.futures.as_completed(future_to_id), 1):
            offer_id = future_to_id[future]
            try:
                offer = future.result()
                if offer:
                    offers.append(offer)
                print(f"Fetched offer {i}/{len(offer_ids)}: {offer_id}")
            except Exception as exc:
                print(f"Offer {offer_id} generated an exception: {exc}")
    return offers

def truncate(s, length=8):
    if not s:
        return "unknown"
    return s[:length] + "..." if len(s) > length else s

def build_label(seller_orders, truncate_fn, max_aliases=2):
    top = sorted(seller_orders.items(), key=lambda x: x[1], reverse=True)[:max_aliases]
    lines = [f"Orders {sum(seller_orders.values())}"]
    for alias, _ in top:
        lines.append(truncate_fn(alias))
    return "\n".join(lines)

def knapsack_liquidity(budget_sats, offers, include_amboss_fee=True):
    chunks = []
    for offer in offers:
        min_size = int(offer['min_size'])
        max_size = int(offer['max_size'])
        base_fee = int(offer['base_fee'])
        fee_rate = int(offer['fee_rate'])
        amboss_fee_rate = int(offer.get('amboss_fee_rate', 0)) if include_amboss_fee else 0
        total_ppm = fee_rate + amboss_fee_rate
        fee_sats = int(min_size * total_ppm / 1_000_000)
        cost = base_fee + fee_sats
        account = offer.get('account', 'unknown')
        allow_parallel = offer.get('allow_parallel', False)
        max_chunks = max_size // min_size
        if not allow_parallel:
            max_chunks = min(max_chunks, 1)
        for _ in range(max_chunks):
            chunks.append({'cost': cost, 'liquidity': min_size, 'account': account})

    n = len(chunks)
    dp = [ (0, {}) for _ in range(budget_sats + 1) ]
    for i in range(n):
        chunk = chunks[i]
        for b in range(budget_sats, chunk['cost'] - 1, -1):
            prev_liq, prev_orders = dp[b - chunk['cost']]
            new_liq = prev_liq + chunk['liquidity']
            if new_liq > dp[b][0]:
                new_orders = prev_orders.copy()
                new_orders[chunk['account']] = new_orders.get(chunk['account'], 0) + 1
                dp[b] = (new_liq, new_orders)
    max_liq, seller_orders = max(dp, key=lambda x: x[0])
    total_cost = 0
    for acct, n in seller_orders.items():
        for chunk in chunks:
            if chunk['account'] == acct:
                total_cost += chunk['cost'] * n
                break
    return max_liq, total_cost, seller_orders

def load_tor_restricted_offer_ids(filename="tor_restricted_offers.txt"):
    if not os.path.exists(filename):
        return set()
    with open(filename, "r") as f:
        return set(line.strip() for line in f if line.strip())

def is_tor_restricted(offer):
    for cond in offer.get("conditions", []):
        if (
            cond.get("condition") == "NODE_SOCKETS"
            and (
                (cond.get("operator") == "NOT_EQUAL_TO" and cond.get("value", "").upper() == "TOR")
                or (cond.get("operator") == "CONTAINS" and cond.get("value", "").upper() == "CLEARNET")
            )
        ):
            return True
    return False

def get_tor_restricted_offer_ids(offer_ids):
    restricted = set()
    for offer_id in offer_ids:
        if is_tor_restricted(offer_id):
            restricted.add(offer_id)
        time.sleep(1)  # Be polite to Amboss, avoid hammering their site
    return restricted

def offer_price_per_sat(offer):
    # Calculate total cost for min_size
    min_size = int(offer['min_size'])
    base_fee = int(offer['base_fee'])
    fee_rate = int(offer['fee_rate'])
    amboss_fee_rate = int(offer['amboss_fee_rate'])
    total_fee_rate = fee_rate + amboss_fee_rate
    # Cost for min_size
    cost = base_fee + (min_size * total_fee_rate // 1_000_000)
    return cost / min_size

def liquidity_for_budget(args):
    usd, btc_usd, offers = args
    budget_sats = usd_to_sats(usd, btc_usd)
    liquidity_sats, _, num_orders = knapsack_liquidity(budget_sats, offers)
    return sats_to_usd(liquidity_sats, btc_usd), num_orders

def generate_liquidity_chart(cache_dir="liquiditychart", cache_filename="liquidity_chart.png", cache_minutes=60, progress_callback=None):
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, cache_filename)
    if os.path.exists(cache_path):
        mtime = os.path.getmtime(cache_path)
        if time.time() - mtime < cache_minutes * 60:
            if progress_callback:
                progress_callback("Using cached chart...")
            return cache_path  # Use cached chart

    if progress_callback:
        progress_callback("Fetching BTC/USD price...")
    btc_usd = get_btc_usd()
    if progress_callback:
        progress_callback("Fetching enabled offers...")
    offer_ids = get_enabled_offer_ids()
    if progress_callback:
        progress_callback(f"Fetching details for {len(offer_ids)} offers...")
    offers = get_all_offer_details(offer_ids)
    offers = [o for o in offers if o]
    for offer, offer_id in zip(offers, offer_ids):
        if offer is not None:
            offer['id'] = offer_id
    if progress_callback:
        progress_callback("Sorting and filtering offers...")
    offers_tor = [o for o in offers if not is_tor_restricted(o)]
    offers_clearnet = offers
    offers_tor_sorted = sorted(offers_tor, key=offer_price_per_sat)
    offers_clearnet_sorted = sorted(offers_clearnet, key=offer_price_per_sat)
    budgets_usd_coarse = np.arange(0, 505, 25)
    budgets_usd_fine = np.linspace(0, 500, 201)
    y_tor_coarse, y_clearnet_coarse = [], []
    costs_tor_coarse, costs_clearnet_coarse = [], []
    for i, usd in enumerate(budgets_usd_coarse):
        if progress_callback and i % 4 == 0:
            progress_callback(f"Calculating liquidity for ${usd}...")
        budget_sats = usd_to_sats(usd, btc_usd)
        liquidity_sats, total_cost_sats, _ = knapsack_liquidity(budget_sats, offers_tor_sorted)
        y_tor_coarse.append(sats_to_usd(liquidity_sats, btc_usd))
        costs_tor_coarse.append(sats_to_usd(total_cost_sats, btc_usd))
        liquidity_sats, total_cost_sats, _ = knapsack_liquidity(budget_sats, offers_clearnet_sorted)
        y_clearnet_coarse.append(sats_to_usd(liquidity_sats, btc_usd))
        costs_clearnet_coarse.append(sats_to_usd(total_cost_sats, btc_usd))
    if progress_callback:
        progress_callback("Rendering chart...")
    y_tor_interp = np.interp(budgets_usd_fine, budgets_usd_coarse, y_tor_coarse)
    y_clearnet_interp = np.interp(budgets_usd_fine, budgets_usd_coarse, y_clearnet_coarse)
    def usd_fmt(x, pos=None):
        return f"${x:,.0f}"
    # --- Amboss-style theming ---
    plt.style.use('dark_background')
    plt.rcParams.update({
        'font.family': 'DejaVu Sans',
        'font.size': 14,
        'axes.labelcolor': 'white',
        'axes.edgecolor': '#222',
        'axes.facecolor': '#181c20',
        'axes.titleweight': 'bold',
        'axes.titlesize': 20,
        'axes.labelsize': 16,
        'xtick.color': '#b8e0ff',
        'ytick.color': '#b8e0ff',
        'grid.color': '#333',
        'legend.fontsize': 14,
        'figure.facecolor': '#181c20',
        'figure.edgecolor': '#181c20',
    })
    plt.figure(figsize=(12,7))
    plt.plot(budgets_usd_fine, y_tor_interp, color='#ff3c7d', label='Tor-Eligible Offers', linewidth=2, linestyle='-')
    plt.plot(budgets_usd_fine, y_clearnet_interp, color='#ffffff', label='All Offers (Clearnet & Tor)', linewidth=2, linestyle='-')
    key_budgets = [10, 50, 100, 500]
    for key_usd in key_budgets:
        idx = np.where(budgets_usd_coarse == key_usd)[0]
        if len(idx) > 0:
            i = idx[0]
            if y_tor_coarse[i] > 0:
                pct = 100 * costs_tor_coarse[i] / y_tor_coarse[i]
                plt.annotate(f"{pct:.2f}%", (budgets_usd_coarse[i], y_tor_coarse[i]), 
                             textcoords="offset points", xytext=(0,-25), ha='center', fontsize=13, color='#ff3c7d',
                             bbox=dict(boxstyle="round,pad=0.2", fc="#222", ec="#ff3c7d", lw=1, alpha=0.8))
            if y_clearnet_coarse[i] > 0:
                pct = 100 * costs_clearnet_coarse[i] / y_clearnet_coarse[i]
                plt.annotate(f"{pct:.2f}%", (budgets_usd_coarse[i], y_clearnet_coarse[i]), 
                             textcoords="offset points", xytext=(0,10), ha='center', fontsize=13, color='#ffffff',
                             bbox=dict(boxstyle="round,pad=0.2", fc="#222", ec="#fff", lw=1, alpha=0.8))
    plt.xlabel('Total Cost (USD)', fontsize=16, color='#b8e0ff', labelpad=10)
    plt.ylabel('Max Liquidity Purchased (USD)', fontsize=16, color='#b8e0ff', labelpad=10)
    plt.title('Magma Liquidity Purchase Power', fontsize=20, color='#fff', pad=15)
    plt.suptitle(f"Tor-restricted offers: {len(offers_clearnet) - len(offers_tor)} out of {len(offers)}", fontsize=12, color='#b8e0ff', y=0.96)
    plt.legend(fontsize=14, loc='best', facecolor='#181c20', edgecolor='#222')
    plt.grid(True, color='#333', linestyle='--', linewidth=0.7)
    plt.tight_layout()
    ax = plt.gca()
    ax.xaxis.set_major_formatter(plt.FuncFormatter(usd_fmt))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(usd_fmt))
    ax.tick_params(axis='both', which='major', labelsize=13, colors='#b8e0ff')
    explanation = (
        "Payment processing fee: Total cost as a percentage of liquidity purchased.\n"
        "For comparison: Visa/interchange fees are typically 1.5–3%, remittance fees 5–10%.\nGenerated by LNhelperBot."
    )
    plt.gcf().text(
        0.5, 0.01, explanation, ha='center', va='bottom', fontsize=12, color='#b8e0ff',
        bbox=dict(boxstyle="round,pad=0.5", fc="#181c20", ec="#222", lw=1, alpha=0.9),
        transform=ax.transAxes
    )
    plt.savefig(cache_path, bbox_inches='tight')
    plt.close()
    return cache_path

if __name__ == "__main__":
    # main()  # Disabled to prevent GUI popups when run directly
    pass
