from unittest.mock import Mock

from app.core.llm.client import LLMClient
from app.core.llm.movie_query_generator import generate_movie_search_query


def test_generate_movie_search_query_appends_still_frame_suffix():
    client = Mock(spec=LLMClient)
    client.load_prompt.side_effect = lambda name: f"<{name}>"
    client.render.side_effect = lambda template, **kwargs: template
    client.generate.return_value = "  Ундина (2009)  \n"

    result = generate_movie_search_query(client, text="рецензия на фильм")

    assert result == "Ундина (2009) кадр из фильма"


def test_generate_movie_search_query_empty_title_returns_empty():
    client = Mock(spec=LLMClient)
    client.load_prompt.side_effect = lambda name: f"<{name}>"
    client.render.side_effect = lambda template, **kwargs: template
    client.generate.return_value = "   "

    assert generate_movie_search_query(client, text="текст без названия") == ""
