from groq import Groq

client = Groq(api_key="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")  # ← вставь свой ключ

try:
    models = client.models.list()
    print("✅ ДОСТУПНЫЕ МОДЕЛИ:")
    for model in models.data:
        print(f"  - {model.id}")
except Exception as e:
    print(f"❌ ОШИБКА: {e}")