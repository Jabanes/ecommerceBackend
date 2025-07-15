import os
import requests
import json
from dotenv import load_dotenv
from datetime import datetime, timezone

def fetch_and_save_raw_products_to_json():
    """
    Fetches products from Shopify, transforms them, and saves them
    to a local 'rawdata.json' file.
    """
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

    # --- Load Credentials ---
    store_url = os.getenv("SHOPIFY_STORE_URL")
    access_token = os.getenv("SHOPIFY_API_PASSWORD")
    output_filename = 'rawdata.json'
    
    if not all([store_url, access_token]):
        print("Error: Ensure SHOPIFY_STORE_URL and SHOPIFY_API_PASSWORD are set in the .env file.")
        return

    # --- 1. Fetch products from Shopify ---
    shopify_api_url = f"https://{store_url}/admin/api/2023-10/products.json"
    headers = { "X-Shopify-Access-Token": access_token, "Content-Type": "application/json" }

    try:
        print(f"Fetching products from {store_url}...")
        response = requests.get(shopify_api_url, headers=headers)
        response.raise_for_status()
        products = response.json().get('products', [])
        print(f"Found {len(products)} products in Shopify.")
    except Exception as e:
        print(f"Error fetching from Shopify: {e}")
        return

    # --- 2. Transform data and save to JSON file ---
    try:
        print(f"Transforming {len(products)} products...")
        all_transformed_products = []
        for product_from_api in products:
            first_variant = product_from_api.get('variants', [{}])[0]
            tags_list = [tag.strip() for tag in product_from_api.get('tags', '').split(',') if tag.strip()]

            transformed_product = {
                '_id': str(product_from_api['id']),
                'id': str(product_from_api['id']),
                'title': product_from_api.get('title'),
                'description': product_from_api.get('body_html', ''),
                'vendor': product_from_api.get('vendor'),
                'productType': product_from_api.get('product_type'),
                'handle': product_from_api.get('handle'),
                'status': product_from_api.get('status', '').upper(),
                'tags': tags_list,
                'images': [{'url': img.get('src'), 'altText': img.get('alt')} for img in product_from_api.get('images', [])],
                'variants': [
                    {
                        'id': str(v.get('id')),
                        'title': v.get('title'),
                        'sku': v.get('sku'),
                        'price': float(v.get('price', 0)),
                        'compareAtPrice': float(v.get('compare_at_price')) if v.get('compare_at_price') else None,
                        'inventory': v.get('inventory_quantity', 0)
                    } for v in product_from_api.get('variants', [])
                ],
                'price': float(first_variant.get('price', 0)),
                'currency': 'ILS',
                'syncedAt': datetime.now(timezone.utc).isoformat()
            }
            all_transformed_products.append(transformed_product)

        # --- Write to JSON file ---
        print(f"Writing data to '{output_filename}'...")
        with open(output_filename, 'w', encoding='utf-8') as f:
            json.dump(all_transformed_products, f, ensure_ascii=False, indent=4)
        
        print(f"Successfully saved raw data to {os.path.abspath(output_filename)}")

    except Exception as e:
        print(f"Error during data transformation or file writing: {e}")


if __name__ == "__main__":
    fetch_and_save_raw_products_to_json() 