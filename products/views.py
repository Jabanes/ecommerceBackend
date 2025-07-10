from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
import json
from bs4 import BeautifulSoup
from dropship_backend.firebase_config import db

# Create your views here.

@csrf_exempt
@require_POST
def process_product(request):
    try:
        data = json.loads(request.body)
        html_description = data.get('description', '')
        
        if not html_description:
            return JsonResponse({'error': 'Description is required'}, status=400)

        # Use BeautifulSoup to parse the HTML
        soup = BeautifulSoup(html_description, 'html.parser')
        
        # --- Example Parsing Logic ---
        # This needs to be adapted to your actual HTML structure.
        parsed_data = {}
        for h2 in soup.find_all('h2'):
            key = h2.text.strip().lower().replace(' ', '_')
            content_node = h2.find_next_sibling()
            if content_node:
                # Extracts text, preserving some structure from lists
                if content_node.name == 'ul':
                    parsed_data[key] = [li.text.strip() for li in content_node.find_all('li')]
                else:
                    parsed_data[key] = content_node.text.strip()
        
        # Add other fields from the original request
        final_product = {**data, 'parsed_description': parsed_data}
        del final_product['description'] # Remove raw html


        # Save to Firestore
        # We can use the product's SKU or another unique ID from `data` as the document ID
        product_id = data.get('sku') or data.get('id')
        if not product_id:
            # If no ID, Firestore will generate one automatically
            doc_ref = db.collection('products').add(final_product)
            document_id = doc_ref[1].id
        else:
            db.collection('products').document(str(product_id)).set(final_product)
            document_id = str(product_id)

        return JsonResponse({'status': 'success', 'product_id': document_id, 'data': final_product})

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
