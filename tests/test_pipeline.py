import argparse
import hashlib
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import make_video as renderer
from font_catalog import CATALOG, FONT_FILES, FONT_ROOT, glyph_report, load_font
from pipeline import workflow
from pipeline.design import extract_lyric_samples, render_keyframes
from video_template import normalize_settings, settings_from_design, unified_design
from PIL import Image, ImageDraw


class PipelineSmokeTest(unittest.TestCase):
    def test_bundled_fonts_and_licenses_are_complete(self):
        self.assertGreaterEqual(len(FONT_FILES), 50)
        for entry in CATALOG:
            with self.subTest(font=entry['id']):
                path = FONT_ROOT / entry['file']
                self.assertTrue(path.is_file())
                self.assertTrue((FONT_ROOT / entry['license_file']).is_file())
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry['sha256'])
                self.assertTrue(load_font(path, 32).getbbox('繁體書法'))

    def test_lyric_samples_are_spread_across_song(self):
        with tempfile.TemporaryDirectory() as temporary:
            lyrics = Path(temporary) / 'song.lrc'
            lyrics.write_text('\n'.join(f'[00:{index:02d}.00]第{index}句' for index in range(10)))
            samples = extract_lyric_samples(lyrics)
            self.assertEqual((len(samples), samples[0], samples[-1]), (5, '第0句', '第9句'))

    def test_bad_settings_rejected(self):
        for value in [-1, 1201, 2.5, float('nan'), float('inf'), '42', True]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_settings({'title_size': value})
        with self.assertRaises(ValueError): normalize_settings({'title_font_id': '../other.ttf'})
        with self.assertRaises(ValueError): normalize_settings({'color': 'white'})

    def test_legacy_timing_and_independent_fonts(self):
        design = unified_design('歌名','歌手',{'title_font_id':'iansui','lyric_size':180},
                                {'font_id':'long_cang','transition':'cut','fade_seconds':0.1,'max_lyric_chars':6})
        self.assertEqual(settings_from_design(design)['lyric_font_id'], 'long_cang')
        self.assertEqual(settings_from_design(design)['title_font_id'], 'iansui')
        self.assertEqual((design['transition'],design['max_lyric_chars']),('cut',6))
        self.assertEqual(design['motion'],'off')

    def test_black_gradient_and_size_scaling(self):
        with patch.object(renderer,'WIDTH',640), patch.object(renderer,'HEIGHT',360):
            bg, scene, mist = renderer.make_scene()
            self.assertEqual(scene,[])
            self.assertIsNone(mist.getbbox())
            # 背景带云雾、暗角和淡墨晕，不再左右对称；只校验尺寸与整体为上亮下暗
            self.assertEqual(bg.size,(640,360))
            top = sum(bg.crop((0,0,640,60)).convert('L').getdata()) / (640*60)
            bottom = sum(bg.crop((0,300,640,360)).convert('L').getdata()) / (640*60)
            self.assertGreater(top,bottom)
            font = str(FONT_FILES['iansui'])
            layout = renderer.compute_layout('測試',[{'text':'繁體歌詞'}],font,font,300,150)
            self.assertLessEqual(layout[2],100)
            self.assertLessEqual(layout[3],50)

    def test_missing_glyphs_draw_using_same_fallback_as_measurement(self):
        font_id='lxgwseal_regular'
        report = glyph_report(font_id,'於是我們繼續')
        self.assertTrue(report['fallback'])
        image=Image.new('L',(600,150));draw=ImageDraw.Draw(image)
        font=load_font(FONT_FILES[font_id],50)
        width=draw.textlength('於是我們繼續',font=font)
        draw.text((0,0),'於是我們繼續',font=font,fill=255,stroke_width=1)
        self.assertGreater(width,0)
        self.assertIsNotNone(image.getbbox())

    def test_portrait_single_character_fits_width(self):
        with patch.object(renderer,'WIDTH',360), patch.object(renderer,'HEIGHT',640):
            font=str(FONT_FILES['iansui'])
            layout=renderer.compute_layout('但',[{'text':'風'}],font,font)
            self.assertLessEqual(layout[0].getlength('但'),360*renderer.TITLE_WIDTH_RATIO)
            self.assertLessEqual(layout[1].getlength('風'),360*renderer.LYRIC_WIDTH_RATIO)

    def test_local_template_and_render_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); audio=root/'sample.wav'; lyrics=root/'approved.lrc'
            with wave.open(str(audio),'wb') as stream:
                stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000);stream.writeframes(b'\0\0'*8000)
            lyrics.write_text('[status:approved]\n[00:00.00]於是我們繼續\n')
            with patch.object(workflow,'JOBS',root/'jobs'), patch('socket.create_connection',side_effect=AssertionError('network forbidden')):
                workflow.command_create(argparse.Namespace(audio=str(audio),title='測試',artist='測試者',lyrics=str(lyrics),job_id='smoke'))
                workflow.command_template(argparse.Namespace(job_id='smoke',title_font_id='iansui',lyric_font_id='masafont_regular',title_size=300,lyric_size=180))
                manifest=workflow.load('smoke')
                cmd=workflow.render_command(manifest,root/'out.mp4','aac')
                self.assertEqual(cmd[cmd.index('--title-font')+1],str(FONT_FILES['iansui']))
                self.assertEqual(cmd[cmd.index('--lyric-font')+1],str(FONT_FILES['masafont_regular']))
                self.assertEqual(cmd[cmd.index('--lyric-size')+1],'180')
                self.assertEqual(cmd[cmd.index('--interlude-motion')+1],'off')
                self.assertEqual(manifest['status'],'DESIGN_SELECTED')
                for i in range(1,6):self.assertTrue((root/f'jobs/smoke/design-previews/option-a-{i}.png').is_file())
                self.assertEqual(len(json.loads((root/'jobs/smoke/design-options.json').read_text())['options']),1)
                self.assertEqual(json.loads((root/'jobs/smoke/render-progress.json').read_text())['status'],'idle')


if __name__=='__main__':unittest.main()
