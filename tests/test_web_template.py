import argparse
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from starlette.requests import Request

import web_app
from pipeline import workflow
from video_template import TEMPLATE_ID


def put_template(job_id, body):
    request=Request({'type':'http','method':'PUT','path':'/template','headers':[]})
    request._body=json.dumps(body).encode()
    coroutine=web_app.save_template(job_id,request)
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError('Unexpected asynchronous operation')


class WebTemplateTest(unittest.TestCase):
    def test_save_restore_and_render_lock_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);audio=root/'song.wav';lyrics=root/'song.lrc'
            with wave.open(str(audio),'wb') as stream:
                stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000);stream.writeframes(b'\0\0'*8000)
            lyrics.write_text('[status:approved]\n[00:00.00]於是我們繼續\n')
            with patch.object(workflow,'JOBS',root/'jobs'),patch('socket.create_connection',side_effect=AssertionError('offline')):
                workflow.command_create(argparse.Namespace(audio=str(audio),title='測試',artist='歌手',lyrics=str(lyrics),job_id='web-test'))
                updates={'title_font_id':'masafont_regular','lyric_font_id':'iansui','title_size':420,'lyric_size':160}
                result=put_template('web-test',updates)
                self.assertTrue(result['ok'])
                self.assertEqual(len(result['previews']),5)
                restored=web_app.get_template('web-test')
                self.assertEqual(restored['settings'],updates)
                self.assertTrue(restored['ready'])
                self.assertEqual(workflow.load('web-test')['selected_design']['template_id'],TEMPLATE_ID)
                for update in [{'lyric_size':-1},{'title_font_id':'not-real'},{'palette':['red']}]:
                    self.assertEqual(put_template('web-test',update).status_code,400)
                data=workflow.load('web-test');data['status']='RENDERING';workflow.save(data)
                self.assertEqual(put_template('web-test',updates).status_code,400)
                self.assertEqual(workflow.load('web-test')['status'],'RENDERING')

    def test_only_local_template_api_and_font_samples(self):
        routes={route.path for route in web_app.app.routes}
        self.assertNotIn('/api/jobs/{job_id}/design',routes)
        self.assertNotIn('/api/jobs/{job_id}/cover/llm',routes)
        self.assertEqual(web_app.config()['offline_template'],True)
        self.assertNotIn('model',web_app.config())
        sample=web_app.font_sample('chen_yuluoyan_2','繁體書法')
        self.assertEqual(sample.media_type,'image/png')
        self.assertTrue(sample.body.startswith(b'\x89PNG'))


if __name__=='__main__':unittest.main()
