"""Перерисовка прогресса в TTY без повреждения вопросов и машинного вывода."""
import io
import os
import json
import pty
import subprocess
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from vpnctl_terminal import ProgressLine,message
from vpnctl_manager import ask
from vpnctl_progress import InstallationProgress,RemovalProgress,CommandProgress

class Terminal(io.StringIO):
    def isatty(self):return True
    def fileno(self):return 2

class LiveProgress(unittest.TestCase):
    def setUp(self):
        self.env=mock.patch.dict(os.environ,{'TERM':'xterm','NO_COLOR':'1'},clear=True);self.env.start()
        self.addCleanup(self.env.stop)
    def test_ticks_redraw_one_line_and_clear_tail(self):
        stream=Terminal();line=ProgressLine()
        with mock.patch('sys.stderr',stream):
            line.write('Длинный этап');line.write('Этап')
            text=stream.getvalue()
            self.assertEqual(text,'\rДлинный этап\rЭтап        ')
            self.assertNotIn('\n',text);self.assertNotIn('\033',text)
            line.finish()
        self.assertTrue(stream.getvalue().endswith('\n'))
    def test_redirect_is_plain_lines(self):
        for stream,term in [(io.StringIO(),'xterm')]:
            with mock.patch('sys.stderr',stream),mock.patch.dict(os.environ,{'TERM':term}):
                line=ProgressLine();line.write('Первый');line.write('Второй',final=True)
            self.assertEqual(stream.getvalue(),'Первый\nВторой\n')
    def test_dumb_tty_redraws_without_ansi_or_newlines_per_tick(self):
        stream=Terminal();line=ProgressLine()
        with mock.patch('sys.stderr',stream),mock.patch.dict(os.environ,{'TERM':'dumb'}):
            line.write('Первый');line.write('Второй',transient=True)
            self.assertNotIn('\n',stream.getvalue());self.assertNotIn('\033',stream.getvalue())
            self.assertEqual(stream.getvalue().count('\r'),2);line.finish()
    def test_redirect_omits_animation_frames_and_keeps_stage_changes(self):
        stream=io.StringIO();line=ProgressLine()
        with mock.patch('sys.stderr',stream):
            line.write('Первый этап')
            for _ in range(5):line.write('Повторный тик',transient=True)
            line.write('Второй этап');line.write('Завершено',final=True)
        self.assertEqual(stream.getvalue(),'Первый этап\nВторой этап\nЗавершено\n')
    def test_warning_closes_current_line(self):
        stream=Terminal();line=ProgressLine()
        with mock.patch('sys.stderr',stream):
            line.write('Этап');message('Предупреждение: проверка',file=sys.stderr)
            line.write('Дальше',final=True)
        self.assertEqual(stream.getvalue(),'\rЭтап\nПредупреждение: проверка\n\rДальше\n')
    def test_prompt_pauses_ticks_and_resumes_after_answer(self):
        stream=Terminal();line=ProgressLine()
        def answer():
            before=stream.getvalue();line.write('Тик во время вопроса')
            self.assertEqual(stream.getvalue(),before);return 'да'
        with mock.patch('sys.stderr',stream),mock.patch('builtins.input',side_effect=answer):
            line.write('Подготовка');self.assertEqual(ask('Продолжить? '),'да')
            message('Предупреждение: повторный вопрос',file=sys.stderr)
            self.assertEqual(ask('Повторить? '),'да')
            line.write('Продолжение',final=True)
        self.assertIn('Подготовка\nПродолжить? ',stream.getvalue());self.assertNotIn('Тик во время вопроса',stream.getvalue())
    def test_narrow_terminal_keeps_bar_on_one_row(self):
        stream=Terminal();line=ProgressLine()
        with mock.patch('sys.stderr',stream),mock.patch('os.get_terminal_size',return_value=os.terminal_size((32,24))):
            line.write('Очень длинное название этапа '+'.'*80+' [###.................]')
            text=stream.getvalue();line.finish()
        self.assertTrue(text.startswith('\r[###.................]'))
        self.assertLessEqual(len(text)-1,31)
    def test_all_reporters_close_line_and_stop_thread(self):
        for reporter in [InstallationProgress(),RemovalProgress(),CommandProgress('Обновление')]:
            stream=Terminal()
            with mock.patch('sys.stderr',stream):
                reporter.stage('Проверка');reporter.heartbeat()
                self.assertNotIn('\n',stream.getvalue());reporter.start();reporter.stop()
            self.assertTrue(stream.getvalue().endswith('\n'));self.assertFalse(reporter.thread.is_alive())

    def test_real_pty_redraws_stderr_and_preserves_json_stdout(self):
        master,slave=pty.openpty()
        script="""
import sys,time,json
sys.path.insert(0,sys.argv[1])
from vpnctl_progress import CommandProgress
progress=CommandProgress('Проверка',interval=0.02)
progress.stage('Выполняется');progress.start();time.sleep(0.12);progress.stop()
progress.finished=True;progress.stage('Завершено')
print(json.dumps({'ok':True}))
"""
        try:
            process=subprocess.run([sys.executable,'-c',script,str(Path(__file__).resolve().parents[1]/'lib')],stdout=subprocess.PIPE,stderr=slave,timeout=5)
            os.close(slave);slave=None;chunks=[]
            while True:
                try:chunk=os.read(master,65536)
                except OSError:break
                if not chunk:break
                chunks.append(chunk)
            output=b''.join(chunks)
            self.assertEqual(process.returncode,0);self.assertEqual(json.loads(process.stdout),{'ok':True})
            self.assertGreater(output.count(b'\r'),3);self.assertEqual(output.count(b'\n'),2)
            self.assertNotIn(b'\033',output);self.assertIn(b'100%',output)
        finally:
            os.close(master)
            if slave is not None:os.close(slave)

if __name__=='__main__':unittest.main()
