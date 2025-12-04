import pytest

from arch_installer.core.command import CommandExecutionResult, SystemCommandRunner
from arch_installer.errors import CommandError


class TestCommandResult:
    def test_should_indicate_success_when_exit_code_is_zero(self):
        result = CommandExecutionResult(
            command="echo hello",
            exit_code=0,
            stdout="hello\n",
            stderr="",
        )
        assert result.success is True

    def test_should_indicate_failure_when_exit_code_is_nonzero(self):
        result = CommandExecutionResult(
            command="false",
            exit_code=1,
            stdout="",
            stderr="",
        )
        assert result.success is False


class TestSystemCommandRunner:
    @pytest.fixture
    def runner(self):
        return SystemCommandRunner()

    def test_should_succeed_when_running_simple_command(self, runner):
        result = runner.run("echo hello")
        assert result.success is True
        assert "hello" in result.stdout

    def test_should_succeed_when_running_command_as_list(self, runner):
        result = runner.run(["echo", "hello", "world"])
        assert result.success is True
        assert "hello world" in result.stdout

    def test_should_raise_error_when_command_fails(self, runner):
        with pytest.raises(CommandError) as exc_info:
            runner.run("false")
        assert exc_info.value.exit_code == 1

    def test_should_return_failure_when_check_disabled(self, runner):
        result = runner.run("false", raise_on_nonzero_exit=False)
        assert result.success is False
        assert result.exit_code == 1

    def test_should_use_env_vars_when_provided(self, runner):
        result = runner.run("echo $TEST_VAR", env_variables={"TEST_VAR": "testvalue"})
        assert "testvalue" in result.stdout

    def test_should_use_working_dir_when_cwd_provided(self, runner, tmp_path):
        result = runner.run("pwd", work_dir=str(tmp_path))
        assert str(tmp_path) in result.stdout

    def test_should_pass_input_when_stdin_provided(self, runner):
        result = runner.run("cat", input_data="test input")
        assert "test input" in result.stdout

    def test_should_capture_stderr_when_command_produces_errors(self, runner):
        result = runner.run("ls /nonexistent", raise_on_nonzero_exit=False)
        assert result.success is False
        assert result.stderr or "No such file" in result.stdout
