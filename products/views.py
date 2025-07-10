from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
import json
from bs4 import BeautifulSoup
from dropship_backend.mongo_config import db

# Create your views here.

@csrf_exempt
@require_POST
def process_product(request):
    try:
        data = json.loads(request.body)
        html_description = data.get('description', '')
        
        if not html_description:
            return JsonResponse({'error': 'Description is required'}, status=400)

        soup = BeautifulSoup(html_description, 'html.parser')
        
        parsed_data = {}
        for h2 in soup.find_all('h2'):
            key = h2.text.strip().lower().replace(' ', '_')
            content_node = h2.find_next_sibling()
            if content_node:
                if content_node.name == 'ul':
                    parsed_data[key] = [li.text.strip() for li in content_node.find_all('li')]
                else:
                    parsed_data[key] = content_node.text.strip()
        
        final_product = {**data, 'parsed_description': parsed_data}
        if 'description' in final_product:
            del final_product['description']

        product_id = final_product.get('id')
        if not product_id:
            return JsonResponse({'error': 'Product ID is missing'}, status=400)

        # Use the Shopify product ID as the MongoDB `_id`
        final_product['_id'] = product_id

        # Save to MongoDB
        products_collection = db['products']
        result = products_collection.replace_one({'_id': product_id}, final_product, upsert=True)
        
        return JsonResponse({
            'status': 'success',
            'product_id': product_id,
            'mongo_result': {
                'acknowledged': result.acknowledged,
                'upserted_id': str(result.upserted_id) if result.upserted_id else None,
                'modified_count': result.modified_count,
            }
        })

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
