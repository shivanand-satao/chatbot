import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from book_pipeline import Meaning, generate_meaning, quality_issues, sections, table_rows


class BookTests(unittest.TestCase):
    def test_table_title_and_headers(self):
        header,rows=table_rows([['Acts',None],['Name','Year'],['Example','2023']])
        self.assertIn('Name | Year',header)
        self.assertEqual(rows,['Example | 2023\n'])

    def test_sections_preserve_text(self):
        text='Introduction\n1. First section\nBody\n2. Second section\nMore\n'
        result=list(sections(text))
        self.assertEqual(''.join(body for _,body in result),text)
        self.assertEqual(len(result),3)

    def test_corrupted_text_flagged(self):
        self.assertIn('unmapped_characters',quality_issues('Long enough text '*10+'(cid:123)'))
        self.assertEqual(quality_issues('Normal readable text '*10),[])

    def test_semantic_schema(self):
        with self.assertRaises(ValueError):
            Meaning.model_validate({'summary':'Not enough fields'})

    def test_invalid_semantic_response_retried(self):
        client=Mock()
        valid=Meaning(summary='Source summary',topics=['Topic'],search_questions=['Question?'],
                      uncertainties=['AI-inferred']).model_dump_json()
        def response(content):
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content=content))])
        client.chat.completions.create.side_effect=[response('{"disclaimer":"extra"}'),response(valid)]
        self.assertEqual(generate_meaning(client,{'text':'Evidence'}).summary,'Source summary')
        self.assertEqual(client.chat.completions.create.call_count,2)

    def test_invalid_semantic_response_has_bounded_retries(self):
        client=Mock()
        client.chat.completions.create.return_value=SimpleNamespace(choices=[
            SimpleNamespace(finish_reason='length',message=SimpleNamespace(content='{}'))])
        with self.assertRaises(ValueError):
            generate_meaning(client,{'text':'Evidence'})
        self.assertEqual(client.chat.completions.create.call_count,3)


if __name__=='__main__':
    unittest.main()