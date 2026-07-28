def test_init_db_command(runner):
    result = runner.invoke(args=["init-db"])
    assert result.exit_code == 0
    assert "Đã khởi tạo" in result.output
