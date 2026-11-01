"""Real PDF -> registered tool -> output hook -> Bale adapter, without network.

All artifacts and profiles are isolated. Synthetic PDFs exercise geometry and
rejection cases; real HD785-7 pages exercise both existing document paths.
"""
from contextlib import contextmanager
from io import BytesIO
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[2]
CORE = Path('C:/Users/win-10/AppData/Local/hermes/hermes-agent')
sys.path[:0] = [str(ROOT), str(CORE), str(CORE/'venv/Lib/site-packages'), str(ROOT/'tools')]
import tools
tools.__path__.append(str(CORE/'tools'))
import pymupdf
from PIL import Image
import render_page as engine
from integrations.hermes import pdf_region, technical_media, technical_image


def load_plugin():
    source = ROOT/'integrations/hermes/plugins/komatso-technical-docs/__init__.py'
    spec = importlib.util.spec_from_file_location('pdf_region_plugin_fixture', source)
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    return plugin


def png():
    stream = BytesIO()
    Image.new('RGB', (10, 20), 'white').save(stream, format='PNG')
    return stream.getvalue()


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'runtime')
        self.root = Path(self.tmp.name)
        (self.root/'tools').mkdir()
        (self.root/'model').mkdir()
        (self.root/'tools/manual_models.json').write_text('{"test":"model"}')
        self.patch = patch.object(engine, 'ROOT', self.root)
        self.patch.start()
        self.env = patch.dict(os.environ, HERMES_SESSION_ID='crop-engine-test')
        self.env.start()
        self.pdf = self.root/'model/test.pdf'

    def tearDown(self):
        self.env.stop()
        self.patch.stop()
        self.tmp.cleanup()

    def make_pdf(self, rotation=0, cropped=False, huge=False):
        with pymupdf.open() as doc:
            page = doc.new_page(width=14000 if huge else 400, height=14000 if huge else 300)
            page.draw_rect(pymupdf.Rect(40,50,140,100), color=None, fill=(1,0,0))
            page.insert_text((180,160), 'PIN 42 TEST', fontsize=10)
            if cropped:
                page.set_cropbox(pymupdf.Rect(20,30,380,280))
            page.set_rotation(rotation)
            doc.save(self.pdf)

    def test_full_page_bytes_and_source_preserved_crop_unique(self):
        self.make_pdf()
        source = self.pdf.read_bytes()
        with pymupdf.open(self.pdf) as doc:
            expected = doc[0].get_pixmap(matrix=pymupdf.Matrix(2.4,2.4), colorspace=pymupdf.csRGB, alpha=False).tobytes('png')
        full = Path(engine.render_page(self.pdf,1,'partbook')['media'])
        self.assertEqual(full.name,'partbook-p1.png')
        self.assertEqual(full.read_bytes(), expected)
        crops = [Path(engine.render_page(self.pdf,1,'partbook',region=[.1,.15,.4,.4])['media']) for _ in range(2)]
        self.assertNotEqual(crops[0],crops[1])
        self.assertEqual(full.read_bytes(),expected)
        self.assertEqual(self.pdf.read_bytes(),source)
        self.assertTrue(all(technical_media._GENERATED_PAGE.fullmatch(p.name) for p in crops))

    def test_rotations_and_offset_cropbox_select_the_same_visible_object(self):
        for rotation in (0,90,180,270):
            for cropped in (False,True):
                with self.subTest(rotation=rotation,cropped=cropped):
                    self.make_pdf(rotation,cropped)
                    with pymupdf.open(self.pdf) as doc:
                        p=doc[0]
                        # Exact known red rectangle, converted to displayed coordinates.
                        rect=pymupdf.Rect(40,50,140,100)
                        if cropped:
                            rect += (-20,-30,-20,-30)
                        rect=rect*p.rotation_matrix
                        region=[rect.x0/p.rect.width,rect.y0/p.rect.height,rect.x1/p.rect.width,rect.y1/p.rect.height]
                    result=engine.render_page(self.pdf,1,'manual_evidence',region=region,dpi=288)
                    with Image.open(result['media']) as im:
                        self.assertEqual(im.getpixel((im.width//2,im.height//2)),(255,0,0))
                        self.assertEqual(im.size, (400,200) if rotation in (0,180) else (200,400))
                    self.pdf.unlink()

    def test_invalid_coordinates_and_dpi_never_render(self):
        self.make_pdf()
        bad_regions=[[],[0,0,1],[0,0,1,1,1],[.5,0,.2,1],[0,.8,1,.4],
                     [0,0,0,1],[-.1,0,.5,1],[0,0,1.1,1],[2,2,3,3],
                     [0,float('nan'),1,1],[0,0,float('inf'),1],[False,0,1,1],['0',0,1,1]]
        with patch.object(pymupdf.Page,'get_pixmap') as render:
            for region in bad_regions:
                with self.subTest(region=region),self.assertRaises(ValueError):
                    engine.render_page(self.pdf,1,'partbook',region=region)
            for dpi in (0,-1,601,float('nan'),float('inf'),True):
                with self.subTest(dpi=dpi),self.assertRaises(ValueError):
                    engine.render_page(self.pdf,1,'partbook',region=[0,0,1,1],dpi=dpi)
            render.assert_not_called()

    def test_resource_limit_checked_before_pixmap(self):
        self.make_pdf(huge=True)
        with patch.object(pymupdf.Page,'get_pixmap') as render:
            for region in (None,[0,0,1,1]):
                with self.assertRaisesRegex(ValueError,'budget'):
                    engine.render_page(self.pdf,1,'partbook',region=region)
            render.assert_not_called()

    def test_foreign_pdf_and_bad_page_rejected(self):
        self.make_pdf()
        foreign=self.root/'other.pdf'
        shutil.copy2(self.pdf,foreign)
        with self.assertRaises(ValueError):engine.render_page(foreign,1,'partbook')
        for page in (0,2,1.5,True):
            with self.assertRaises(ValueError):engine.render_page(self.pdf,page,'partbook')

    def test_failed_png_validation_never_publishes_partial_file(self):
        self.make_pdf()
        with patch.object(pymupdf.Pixmap,'save',side_effect=lambda p:Path(p).write_bytes(b'broken')):
            with self.assertRaises(ValueError):engine.render_page(self.pdf,1,'partbook',region=[0,0,.3,.3])
        self.assertEqual(list((self.root/'artifacts/crop-engine-test').iterdir()),[])


class PNGValidationTests(unittest.TestCase):
    def test_valid_small_png_accepted(self):
        data=png()
        self.assertLess(len(data),50000)
        self.assertTrue(technical_image.validate_png(data)['image_validated'])

    def test_empty_corrupt_and_truncated_rejected(self):
        data=png()
        damaged=bytearray(data);damaged[-20]^=1
        for bad in (b'',b'broken',data[:24],data[:-12],data[:len(data)//2],bytes(damaged)):
            with self.subTest(size=len(bad)),self.assertRaises(ValueError):technical_image.validate_png(bad)
        stream=BytesIO();Image.new('RGB',(2,2)).save(stream,format='JPEG')
        with self.assertRaises(ValueError):technical_image.validate_png(stream.getvalue())

    def test_oversize_header_rejected_before_decompression(self):
        data=png()
        chunk=b'IHDR'+struct.pack('>II',10001,20)+data[24:29]
        forged=data[:8]+struct.pack('>I',13)+chunk+struct.pack('>I',zlib.crc32(chunk))+data[33:]
        with self.assertRaisesRegex(ValueError,'budget'):technical_image.validate_png(forged)
        with patch.object(technical_image,'MAX_PNG_BYTES',10), self.assertRaises(ValueError):
            technical_image.validate_png(data)

    def test_missing_and_empty_file_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as tmp:
            p=Path(tmp)/'empty.png'
            with self.assertRaises(ValueError):technical_image.read_validated_png(p)
            p.touch()
            with self.assertRaises(ValueError):technical_image.read_validated_png(p)

    def test_transport_caps_are_separate_from_decoder_budgets(self):
        image={'image_bytes':100,'image_width':400,'image_height':300}
        self.assertFalse(technical_image.upload_requires_document(image))
        self.assertTrue(technical_image.upload_requires_document(image|{'image_bytes':10_000_001}))
        with self.assertRaises(ValueError):
            technical_image.upload_requires_document(image|{'image_bytes':50_000_001})
        self.assertTrue(technical_image.upload_requires_document(image|{'image_width':10000,'image_height':1},'telegram'))


class PipelineTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        from hermes_constants import set_hermes_home_override,reset_hermes_home_override,hermes_home_key
        from hermes_cli.plugins import PluginManager,PluginManifest,PluginContext,LoadedPlugin
        with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as tmp, tempfile.TemporaryDirectory(prefix='pdf-region-',dir=ROOT/'artifacts') as output:
            home=Path(tmp)/'profiles/admin'
            home.mkdir(parents=True)
            (home/'image_cache').mkdir()
            config={'plugins':{'enabled':['komatso-technical-docs']},
                    'platform_toolsets':{'bale':['komatso_technical_docs'],'telegram':['komatso_technical_docs']},
                    'gateway':{'strict':True,'trust_recent_files':False,'media_delivery_exclusive_roots':True,
                               'media_delivery_allow_dirs':[str(home/'image_cache')]}}
            (home/'config.yaml').write_text(json.dumps(config))
            plugin=load_plugin()
            manager=PluginManager(hermes_home_key(home))
            manifest=PluginManifest(name='komatso-technical-docs',source='project')
            manager._plugins[manifest.name]=LoadedPlugin(manifest=manifest,enabled=True)
            plugin.register(PluginContext(manifest,manager))
            manager._discovered=True
            token=set_hermes_home_override(home)
            try:
                with patch('hermes_cli.plugins.get_plugin_manager',return_value=manager):
                    yield plugin,home,Path(output)
            finally:reset_hermes_home_override(token)

    def args(self,source='parts_book',**kwargs):
        return dict(model='HD785-7',source=source,page=550 if source=='parts_book' else 61,
                    region=[.14,.23,.26,.42],**({'book_id':'HD785-7-B1'} if source=='parts_book' else {}),**kwargs)

    def test_registered_tool_real_parts_and_manual_crops(self):
        from agent.turn_finalizer import apply_llm_output_transform
        from gateway.platforms.base import BasePlatformAdapter
        with self.fixture() as (plugin,home,output):
            cases=[self.args(),self.args()|{'region':[.505,.25,.8,.36]},
                   self.args('shop_manual')|{'page':1331,'region':[.13,.065,.21,.2]},
                   self.args('shop_manual')|{'region':[.15,.12,.93,.32]},
                   self.args()|{'region':[.19,.275,.24,.325],'dpi':600}]
            old_schemas=(plugin.tool_schema(plugin.manual_models()),plugin.part_schema(plugin.part_model_names()))
            image_sizes=[]
            for index,args in enumerate(cases):
                result=json.loads(plugin.region_evidence(args,session_id=output.name))
                self.assertTrue(result.get('success'),result)
                self.assertEqual(len(result['media']),1)
                image_sizes.append(result['image_bytes'])
                raw=Path(result['media'][0][6:]).read_bytes()
                text='[[as_document]]\n'+result['media'][0]
                agent=SimpleNamespace(session_id=output.name,model='fixture',platform='bale')
                final=apply_llm_output_transform(agent,text,turn_id=str(index))[0]
                files,_=BasePlatformAdapter.extract_media(final)
                allowed=BasePlatformAdapter.filter_media_delivery_paths(files)
                self.assertEqual(len(allowed),1,final)
                path=Path(allowed[0][0])
                self.assertTrue(path.is_relative_to(home))
                self.assertEqual(path.read_bytes(),raw)
                asyncio.run(self.transport(path,raw,document=True))
                asyncio.run(self.transport(path,raw,document=False))
            self.assertTrue(any(size<50000 for size in image_sizes),image_sizes)
            self.assertEqual(old_schemas,(plugin.tool_schema(plugin.manual_models()),plugin.part_schema(plugin.part_model_names())))

    async def transport(self,path,raw,document):
        # Actual Bale/Telegram local-file send methods; only the network is fake.
        source=ROOT/'integrations/hermes/plugins/bale/adapter.py'
        spec=importlib.util.spec_from_file_location('crop_bale_fixture',source)
        bale=importlib.util.module_from_spec(spec);spec.loader.exec_module(bale)
        adapter=object.__new__(bale.BaleAdapter)
        sent=[]
        async def receive(**kwargs):
            stream=kwargs.get('document',kwargs.get('photo'))
            sent.append(stream.read())
            return SimpleNamespace(message_id=42)
        async def send(method,chat_id,reply_to,metadata,label,**kwargs):
            kwargs.pop('reset_media',None)
            return await method(**kwargs)
        adapter._bot=SimpleNamespace(send_photo=receive,send_document=receive)
        adapter._send_media=send
        if document:result=await adapter.send_document('offline',str(path))
        else:result=await adapter.send_image_file('offline',str(path))
        self.assertTrue(result.success,result)
        self.assertEqual(sent,[raw])

    def test_rejected_profile_path_book_model_and_inputs(self):
        with self.fixture() as (plugin,home,output):
            cases=[self.args()|{'pdf_path':'C:/private.pdf'},self.args()|{'book_id':'../../secret'},
                   self.args()|{'model':'HD785'},self.args()|{'model':'HD605-7R'},
                   self.args()|{'region':[0,0,1.1,1]},self.args()|{'region':[0,0,float('nan'),1]},
                   self.args()|{'page':0},self.args()|{'source':'arbitrary'},self.args()|{'dpi':601}]
            for args in cases:
                with self.subTest(args=args):
                    result=json.loads(plugin.region_evidence(args,session_id=output.name))
                    self.assertFalse(result.get('success'),result)
                    self.assertNotIn('media',result)
            self.assertFalse(json.loads(plugin.region_evidence(self.args(),session_id='../foreign')).get('success'))
            (home/'config.yaml').write_text('{}')
            self.assertFalse(json.loads(plugin.region_evidence(self.args(),session_id=output.name)).get('success'))

    def test_name_filter_and_corrupt_image_cannot_become_media(self):
        with self.fixture() as (plugin,home,output):
            valid=output/'partbook-p550-crop-0123456789-0123456789abcdef0123456789abcdef.png'
            valid.write_bytes(png())
            self.assertTrue(technical_media._GENERATED_PAGE.fullmatch(valid.name))
            for name in ('private.png','partbook-p1-crop-anything.png','partbook-p0-crop-0123456789-0123456789abcdef0123456789abcdef.png'):
                self.assertFalse(technical_media._GENERATED_PAGE.fullmatch(name))
            with self.assertRaises((OSError,ValueError)):technical_media._publish_page(valid,home,output.name+'foreign')
            valid.write_bytes(png()[:24])
            for exclusive in (True,False):
                cfg=json.loads((home/'config.yaml').read_text());cfg['gateway']['media_delivery_exclusive_roots']=exclusive
                (home/'config.yaml').write_text(json.dumps(cfg))
                final=technical_media.transform_technical_media('MEDIA:'+valid.as_posix(),session_id=output.name,platform='bale')
                self.assertNotIn('MEDIA:',final)

    def test_transport_document_fallback_uses_existing_marker(self):
        with self.fixture() as (plugin,home,output):
            valid=output/'partbook-p550-crop-0123456789-0123456789abcdef0123456789abcdef.png'
            valid.write_bytes(png())
            with patch.object(technical_image,'PHOTO_UPLOAD_BYTES',1):
                final=technical_media.transform_technical_media('MEDIA:'+valid.as_posix(),session_id=output.name,platform='bale')
                self.assertIn('[[as_document]]',final)
                self.assertIn('MEDIA:',final)
            with patch.object(technical_image,'DOCUMENT_UPLOAD_BYTES',1):
                final=technical_media.transform_technical_media('MEDIA:'+valid.as_posix(),session_id=output.name,platform='bale')
                self.assertNotIn('MEDIA:',final)

    def test_other_artifact_delivery_keeps_existing_profile_policy(self):
        with self.fixture() as (plugin,home,output):
            config=json.loads((home/'config.yaml').read_text())
            config['gateway']['media_delivery_exclusive_roots']=False
            (home/'config.yaml').write_text(json.dumps(config))
            report=output/'daily_report.pdf'
            report.write_bytes(b'%PDF-1.4\nfixture')
            text='MEDIA:'+report.as_posix()
            self.assertIsNone(technical_media.transform_technical_media(text,session_id=output.name,platform='bale'))

    def test_tool_registered_for_each_existing_enabled_profile(self):
        plugin=load_plugin()
        registered=[]
        class Context:
            def register_hook(self,*a,**k):pass
            def register_system_prompt_section(self,*a,**k):pass
            def register_tool(self,*a,**k):registered.append(k if k else a)
        plugin.register(Context())
        self.assertIn(pdf_region.REGION_TOOL,str(registered))
        schema=pdf_region.region_schema()['parameters']
        self.assertFalse(schema['additionalProperties'])
        self.assertNotIn('pdf_path',schema['properties'])

    def test_crop_and_media_work_for_all_existing_profiles(self):
        from hermes_constants import set_hermes_home_override,reset_hermes_home_override
        from gateway.platforms.base import BasePlatformAdapter
        with self.fixture() as (plugin,admin,output):
            for name in ('default','maintenance','admin','net'):
                home=admin if name=='admin' else admin.parent/name
                home.mkdir(exist_ok=True)
                if name!='admin':
                    config=json.loads((admin/'config.yaml').read_text())
                    config['gateway']={'strict':False,'trust_recent_files':False,
                                       'media_delivery_allow_dirs':[str(ROOT/'artifacts')]}
                    (home/'config.yaml').write_text(json.dumps(config))
                token=set_hermes_home_override(home)
                try:
                    answer=json.loads(plugin.region_evidence(self.args(),session_id=output.name))
                    self.assertTrue(answer.get('success'),answer)
                    text=answer['media'][0]
                    transformed=technical_media.transform_technical_media(text,session_id=output.name,platform='bale') or text
                    files,_=BasePlatformAdapter.extract_media(transformed)
                    allowed=BasePlatformAdapter.filter_media_delivery_paths(files)
                    self.assertEqual(len(allowed),1,(name,transformed))
                    self.assertEqual(Path(allowed[0][0]).is_relative_to(home),name=='admin')
                finally:reset_hermes_home_override(token)


if __name__=='__main__':unittest.main()
