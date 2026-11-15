"""Real-source ROC acceptance; isolated index and no operational transport."""
import contextlib
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest
import tempfile
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
HOME=Path('C:/Users/win-10/AppData/Local/hermes')
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'tools/fleet'),str(ROOT),str(HOME/'hermes-agent'),str(HOME/'hermes-agent/venv/Lib/site-packages')]
import pymupdf
import manual_evidence_probe as manual
import partbook_lookup as parts
import partbook_atlas as atlas
from integrations.hermes.technical_documents import resolve_document,choose_document,page_allowed,diagram_search
from integrations.hermes import technical_documents
from integrations.hermes import pdf_region
from integrations.hermes.technical_image import read_validated_png
from integrations.hermes import technical_media

DB=Path(os.environ.get('PARTBOOK_TEST_DB',ROOT/'runtime/roc-l8-onboarding/acceptance-final.sqlite'))
MAP=ROOT/'ROC-L8/manual_sections.json'
META=json.loads(MAP.read_text(encoding='utf-8'))

def lookup(*args,model='ROC-L8'):
    with contextlib.redirect_stdout(io.StringIO()):return parts.main(['--model',model,'--db',str(DB),*args])

def probe(question,component=''):
    terms=manual.search_terms(question,component)
    chosen=manual.route(META['sections'],terms,question)
    return manual.probe(MAP,META,chosen,terms,top=6)

class ManualAcceptance(unittest.TestCase):
    def test_feed_has_l830_and_no_l740_only_page(self):
        r=probe('فید ROC-L8','feed')
        self.assertLess(r['searched_pages'],40)
        self.assertFalse(r['full_manual_fallback'])
        pages={e['pdf_page'] for e in r['evidence']}
        self.assertIn(118,pages);self.assertNotIn(117,pages);self.assertNotIn(116,pages)
    def test_compressor(self):
        r=probe('کمپرسور','compressor')
        self.assertTrue({93,95}&{e['pdf_page'] for e in r['evidence']})
        self.assertLess(r['searched_pages'],40)
    def test_hydraulic_l8_filter_even_in_broad_scan(self):
        chosen=list(manual.nodes(META['sections']))
        r=manual.probe(MAP,META,chosen,['hydraulic','l7','l8'],top=10)
        self.assertTrue(all(page_allowed(META,e['pdf_page']) for e in r['evidence']))
        self.assertFalse(any(304<=h['pdf_page']<=311 for h in r['top_page_index']))
        self.assertNotIn('hydraulic_diagrams_l7',{s['key'] for s in r['searched_sections']})
    def test_plc_l830(self):
        r=probe('PLC L830','plc programme')
        self.assertTrue(r['evidence'])
        self.assertTrue(all(292<=e['pdf_page']<=303 for e in r['evidence']))
    def test_explicit_l7_and_filemarket_rejected(self):
        for p in (10,64,117,135,266,278,304,360,369):
            with self.subTest(page=p),self.assertRaises(ValueError):manual.batch_pages(MAP,META,[p])
    def test_unknown_topic_has_no_fabricated_evidence(self):
        r=probe('UNSUPPORTEDOVERHAULTOLERANCE')
        self.assertEqual(r['evidence'],[]);self.assertEqual(r['searched_pages'],0)
    def test_existing_pc800_and_hd785_manual(self):
        for model,q in [('PC800-8R','engine starting'),('HD785-5','retarder')]:
            m=ROOT/manual.MODELS[model]/'manual_sections.json';d=json.loads(m.read_text(encoding='utf-8'))
            terms=manual.search_terms(q);r=manual.probe(m,d,manual.route(d['sections'],terms,q),terms)
            self.assertTrue(r['evidence']);self.assertFalse(r['full_manual_fallback'])

class PartsAcceptance(unittest.TestCase):
    def test_spaced_unspaced_leading_zero(self):
        a=lookup('--part-number','0147 1405 03','--verify');b=lookup('--part-number','0147140503','--verify')
        self.assertTrue(a['found']);self.assertEqual(a['candidates'],b['candidates'])
        self.assertEqual(a['candidates'][0]['part_number'],'0147140503')
        self.assertEqual(a['candidates'][0]['document_verification_status'],'VERIFIED_IN_DOCUMENT')
        self.assertFalse(a['part_applicability_confirmed'])
    def test_default_one_book_and_ref_four_differ(self):
        a=lookup('--figure','3222 3060 41','--item','4','--verify')
        b=lookup('--book','ROC-L8-LF-D3','--figure','3222 3288 28','--item','4','--verify')
        self.assertEqual([x['book_id'] for x in a['selected_part_books']],['ROC-L8-D2'])
        self.assertEqual(a['candidates'][0]['part_number_raw'],'0147 1405 03')
        self.assertEqual(a['candidates'][0]['pdf_pages'],[12])
        self.assertEqual(b['candidates'][0]['part_number_raw'],'0147 1960 69')
        self.assertEqual(b['candidates'][0]['pdf_pages'],[33])
    def test_lf_alias_selects_only_lf(self):
        r=lookup('--query','WAGON FRAME','--item-ref','4',model='ROC L8(30)LF')
        self.assertEqual([b['book_id'] for b in r['selected_part_books']],['ROC-L8-LF-D3'])
    def test_legacy_reader_default_cannot_expand_to_two_books(self):
        from partbook_resolver import resolve_model
        asset=resolve_model('ROC-L8')['fleet_asset']
        self.assertEqual(asset['book_ids'],['ROC-L8-D2'])
        self.assertEqual(asset['supplemental_book_ids'],['ROC-L8-LF-D3'])
    def test_alphanumeric_ref_and_unreadable_pn(self):
        r=lookup('--book','ROC-L8-LF-D3','--query','ELECTR. SYSTEM','--item-ref','E01','--verify')
        self.assertTrue(r['found']);self.assertEqual(r['candidates'][0]['item_ref'],'E01')
        self.assertIsNone(r['candidates'][0]['part_number'])
        self.assertFalse(r['part_found_in_source'])
    def test_printed_dash_ref_and_explicit_no_parts_page(self):
        for bid,page in [('ROC-L8-D2',160),('ROC-L8-LF-D3',279),('ROC-L8-LF-D3',1139)]:
            r=lookup('--book',bid,'--part-number','8231 1206 98','--item-ref','-','--verify')
            matches=[c for c in r['candidates'] if c['pdf_pages']==[page]]
            self.assertTrue(matches,(bid,page));self.assertEqual(matches[0]['pdf_verification']['status'],'VERIFIED')
        with sqlite3.connect(DB) as con:
            for page in [245,247,249,251,903,905,907,909]:
                self.assertEqual(con.execute('select count(*) from part_occurrences where book_id=? and pdf_page=?',('ROC-L8-LF-D3',page)).fetchone()[0],0)
    def test_view_table_and_continuation(self):
        r=lookup('--figure','3222 3060 41','--item','4','--verify')['candidates'][0]
        self.assertIn(11,r['view_pages']);self.assertIn(12,r['table_pages']);self.assertIn(13,r['table_pages'])
        self.assertFalse(r['table_bbox_is_illustration_location'])
        self.assertEqual(r['illustration_references'][0]['drawing'],'B0013128')
        self.assertEqual(r['table_references'][0]['printed_page'],'11')
    def test_see_page_is_document_specific(self):
        r=lookup('--figure','3222 3060 41','--item','6','--verify')['candidates'][0]
        self.assertEqual(r['see_page_targets'][0]['pdf_pages'],[16])
        r=lookup('--book','ROC-L8-LF-D3','--figure','3222 3288 28','--item','6','--verify')['candidates'][0]
        self.assertEqual(r['see_page_targets'][0]['pdf_pages'],[37])
    def test_returned_atlas_figure_can_be_reused(self):
        row=lookup('--part-number','3222311562')['candidates'][0]
        result=lookup('--figure',row['figure'],'--item-ref','2','--verify')
        self.assertTrue(result['candidates'])
        self.assertEqual(result['candidates'][0]['part_number'],'3222311562')
    def test_miss_does_not_search_both_or_claim_absence(self):
        r=lookup('--part-number','0000000000000000')
        self.assertEqual(r['found'],0);self.assertEqual(len(r['selected_part_books']),1)
        self.assertIn('NOT evidence of absence',r['miss'])
    def test_old_rows_verification(self):
        for model,pn in [('PC800-8R','209-01-42232'),('HD785-5','6212-11-1103')]:
            r=lookup('--part-number',pn,'--verify',model=model)
            self.assertTrue(r['candidates']);self.assertTrue(all(c['pdf_verification']['status']=='VERIFIED' for c in r['candidates']))

class ElectricalAcceptance(unittest.TestCase):
    def test_all_63_and_d1_44_have_independent_records(self):
        d=json.loads((ROOT/'ROC-L8/diagram_index.json').read_text(encoding='utf-8'))['sheets']
        for ident,count in [('roc-l8-cabin-9840042409',16),('roc-l8-electrical-9840042418',47)]:
            records=[r for r in d if r['document_id']==ident]
            self.assertEqual({r['pdf_page'] for r in records},set(range(1,count+1)))
            self.assertTrue(all(r['verification_status']=='VISUAL_TITLE_AND_SHEET_VERIFIED' for r in records))
        self.assertEqual(len([r for r in d if r['document_id']==META['document_id'] and r['sheet_total']==44]),44)
    def test_x50_and_b362(self):
        for q,ident,page in [('X50','roc-l8-cabin-9840042409',2),('B362','roc-l8-electrical-9840042418',30)]:
            self.assertEqual(choose_document(MAP.parent,META,q),ident)
            selected=resolve_document(MAP.parent,META,ident)
            hits=diagram_search(MAP.parent,selected,q)
            self.assertIn(page,[h['pdf_page'] for h in hits]);self.assertTrue(all(h['image_inspection_required'] for h in hits))
    def test_unknown_document_and_path_rejected(self):
        for ident in ['D5','../../PC800/foo.pdf','C:\\secret.pdf']:
            with self.assertRaises(ValueError):resolve_document(MAP.parent,META,ident)
    def test_model_filter_air_and_hydraulic(self):
        self.assertFalse(page_allowed(META,304,visual=True));self.assertTrue(page_allowed(META,312,visual=True))
        self.assertFalse(page_allowed(META,360,visual=True));self.assertTrue(page_allowed(META,366,visual=True))

class PluginAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=ROOT/'integrations/hermes/plugins/komatso-technical-docs/__init__.py'
        spec=importlib.util.spec_from_file_location('roc_plugin_test',source)
        cls.plugin=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.plugin)
    def test_bare_atlas_pn_is_part_intent(self):
        self.assertEqual(self.plugin.source_intent('ROC-L8 3222 3115 62'),'part')
        self.assertEqual(self.plugin.source_intent('ROC-L8 3222311562'),'part')
        self.assertEqual(self.plugin.source_intent('PC800 serial 1234567890 engine pressure'),'technical')
    def test_plugin_default_and_explicit_lf_keep_source_identity(self):
        import hermes_constants
        with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as tmp:
            home=Path(tmp);(home/'config.yaml').write_text(json.dumps({'plugins':{'enabled':['komatso-technical-docs']},'platform_toolsets':{'bale':['komatso_technical_docs']}}))
            with patch.object(hermes_constants,'get_hermes_home',return_value=home),patch.dict(os.environ,PARTBOOK_TEST_DB=str(DB)):
                for lf,bid in [('', 'ROC-L8-D2'),(' LF','ROC-L8-LF-D3')]:
                    r=json.loads(self.plugin.part_lookup(dict(model='ROC-L8',question='ROC-L8 3222 3115 62'+lf,part_number='3222 3115 62'),session_id='roc-source'+('lf' if lf else 'main')))
                    self.assertEqual([b['book_id'] for b in r['selected_part_books']],[bid])
                    self.assertTrue(r['candidates']);self.assertEqual(len(r['media']),1)
                    self.assertEqual(r['rendered'][0]['document_id'],r['candidates'][0]['document_id'])
    def test_actual_retrieve_finish_is_bound_to_cabin_document_and_session(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as tmp:
            home=Path(tmp);(home/'config.yaml').write_text(json.dumps({'plugins':{'enabled':['komatso-technical-docs']},'platform_toolsets':{'bale':['komatso_technical_docs']}}))
            sid='roc-acceptance-cabin'
            r=self.plugin.retrieve(dict(model='ROC-L8',question='نقشه برق کابین ROC-L8 و X50',keywords='X50 cabin wiring'),home,sid)
            self.assertEqual(r['prepared']['document_id'],'roc-l8-cabin-9840042409')
            self.assertEqual(r['evidence_coverage']['status'],'visual_required')
            rid=r['prepared']['request_id']
            with self.assertRaises(ValueError):self.plugin.finish(dict(request_id=rid,render_pages=[2]),'foreign-session')
            with self.assertRaises(ValueError):self.plugin.finish(dict(request_id=rid,document_id=META['document_id'],render_pages=[2]),sid)
            final=self.plugin.finish(dict(request_id=rid,render_pages=[2]),sid)
            packet=final['rendered_pages']
            self.assertEqual(packet['document_id'],'roc-l8-cabin-9840042409')
            page=packet['pages'][0];path=Path(page['media']);self.assertEqual(path.parent.name,sid)
            self.assertTrue(technical_media._GENERATED_PAGE.fullmatch(path.name));read_validated_png(path)
    def test_real_diagram_crop_and_path_scope_guards(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as tmp:
            home=Path(tmp);(home/'config.yaml').write_text(json.dumps({'plugins':{'enabled':['komatso-technical-docs']},'platform_toolsets':{'bale':['komatso_technical_docs']}}))
            args=dict(model='ROC-L8',source='shop_manual',document_id='roc-l8-cabin-9840042409',page=2,region=[.72,.08,.86,.71],dpi=288)
            r=pdf_region.render_region(args,session_id='roc-acceptance-crop',home=home,resolver=self.plugin._resolver)
            self.assertEqual(r['document_id'],args['document_id']);self.assertTrue(r['success']);self.assertGreater(r['image_width'],400)
            with self.assertRaises(ValueError):pdf_region.render_region(dict(args,pdf_path='C:/private.pdf'),session_id='roc-acceptance-crop',home=home,resolver=self.plugin._resolver)
            with self.assertRaises(ValueError):pdf_region.render_region(dict(args,document_id=META['document_id'],page=304),session_id='roc-acceptance-crop',home=home,resolver=self.plugin._resolver)
            with self.assertRaises(ValueError):pdf_region.render_region(dict(args,page=17),session_id='roc-acceptance-crop',home=home,resolver=self.plugin._resolver)
            # Native vision receives the exact validated bytes, with an explicit
            # refusal summary when the provider cannot carry images.
            packet=technical_documents.image_evidence_packet(r,r['media'],'roc-acceptance-crop')
            images=[p for p in packet['content'] if p['type']=='image_url']
            data=base64.b64decode(images[0]['image_url']['url'].split(',',1)[1])
            self.assertEqual(data,Path(r['media'][0][6:]).read_bytes())
            self.assertTrue(packet['_multimodal'])
            self.assertIn('IMAGES_NOT_VISIBLE_IN_TEXT_FALLBACK',packet['text_summary'])
            with self.assertRaises((OSError,ValueError)):
                technical_documents.image_evidence_packet(r,r['media'],'foreign-session')
            import tools
            core_tools=str(HOME/'hermes-agent/tools')
            if core_tools not in tools.__path__:tools.__path__.append(core_tools)
            from agent.vision_message_prep import VisionMessagePrepMixin
            class TextOnly(VisionMessagePrepMixin):
                provider='text-fixture';model='text-fixture'
                def _model_supports_vision(self):return False
            fallback=json.loads(TextOnly()._tool_result_content_for_active_model('maintenance_manual_evidence',packet))
            self.assertEqual(fallback['image_context_status'],'IMAGES_NOT_VISIBLE_IN_TEXT_FALLBACK')

class SourceIntegrity(unittest.TestCase):
    def test_changed_source_or_map_never_reuses_registered_identity(self):
        import hashlib
        with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as tmp:
            root=Path(tmp);folder=root/'device';folder.mkdir();pdf=folder/'source.pdf'
            pdf.write_bytes(b'original source bytes')
            manifest={'documents':[dict(document_id='registered',file_path='device/source.pdf',document_type='technical_training',page_count=1,sha256=hashlib.sha256(pdf.read_bytes()).hexdigest())]}
            (folder/'document_manifest.json').write_text(json.dumps(manifest))
            metadata=dict(document_id='registered',manual=str(pdf),pdf_pages=1)
            with patch.object(technical_documents,'ROOT',root):
                self.assertEqual(resolve_document(folder,metadata)['document_id'],'registered')
                with self.assertRaises(ValueError):resolve_document(folder,dict(metadata,pdf_pages=2))
                pdf.write_bytes(b'changed source bytes')
                with self.assertRaises(ValueError):resolve_document(folder,metadata)

if __name__=='__main__':unittest.main()
