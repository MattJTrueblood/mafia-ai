"""Fetch models that support structured outputs from OpenRouter API."""

import requests
import json

try:
    response = requests.get('https://openrouter.ai/api/v1/models')
    data = response.json()
    models = data.get('data', [])
    
    structured_models = []
    
    for model in models:
        model_id = model.get('id', '')
        
        # Check if model supports structured outputs
        # This is typically indicated by supporting 'response_format' parameter
        # or having structured_outputs in supported features
        supported_params = model.get('supported_parameters', [])
        
        # OpenRouter models that support structured outputs typically have
        # 'response_format' in supported_parameters or support JSON schema
        supports_structured = (
            'response_format' in supported_params or
            'json_schema' in str(model).lower() or
            'structured_output' in str(model).lower()
        )
        
        # Also check architecture or other indicators
        architecture = model.get('architecture', {})
        
        # Many models support structured outputs via response_format parameter
        # Let's check the actual API documentation pattern - models that support
        # structured outputs usually have response_format in their supported parameters
        
        # Actually, let me check if there's a specific field for this
        # Based on OpenRouter docs, structured outputs are supported via response_format
        if 'response_format' in supported_params:
            pricing = model.get('pricing', {})
            prompt_price = float(pricing.get('prompt', 0)) * 1_000_000
            completion_price = float(pricing.get('completion', 0)) * 1_000_000
            
            structured_models.append({
                'id': model_id,
                'name': model.get('name', ''),
                'input_price': prompt_price,
                'output_price': completion_price,
                'context_length': model.get('context_length', 0)
            })
    
    # Sort by model ID
    structured_models.sort(key=lambda x: x['id'])
    
    # Write to text file
    with open('structured_output_models.txt', 'w', encoding='utf-8') as f:
        f.write("OpenRouter Models with Structured Outputs Support\n")
        f.write("=" * 100 + "\n\n")
        f.write(f"Total models found: {len(structured_models)}\n\n")
        f.write(f"{'Model ID':<60} {'Input ($/1M)':<15} {'Output ($/1M)':<15} {'Context':<10}\n")
        f.write("-" * 100 + "\n")
        
        for m in structured_models:
            f.write(f"{m['id']:<60} ${m['input_price']:<14.2f} ${m['output_price']:<14.2f} {m['context_length']:<10}\n")
    
    print(f"Found {len(structured_models)} models with structured outputs support")
    print("Results written to structured_output_models.txt")
    
    # Also print first 20 as preview
    print("\nPreview (first 20):")
    print(f"{'Model ID':<60} {'Input ($/1M)':<15} {'Output ($/1M)':<15}")
    print("-" * 90)
    for m in structured_models[:20]:
        print(f"{m['id']:<60} ${m['input_price']:<14.2f} ${m['output_price']:<14.2f}")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

