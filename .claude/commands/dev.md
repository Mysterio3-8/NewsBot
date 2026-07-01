---
description: Запустить AI News Rewriter локально (десктоп UI)
---
1. Убедиться, что виртуальное окружение активно (`venv\Scripts\activate` на Windows).
2. Убедиться, что Ollama запущена и модель из `config.yaml` (`llm.model`) скачана — проверить `ollama list`.
3. Запустить `python app/main.py`.
4. Если файла `app/main.py` ещё нет — сказать, что Этап 0 (каркас) ещё не реализован, и предложить начать с него, сославшись на раздел 18 и 21 в SPEC.md.
