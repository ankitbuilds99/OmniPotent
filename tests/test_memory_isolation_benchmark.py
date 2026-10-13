from scripts.benchmark_memory_isolation import run


def test_memory_isolation_benchmark_reports_no_cross_tenant_reads():
    result = run(3)
    assert result["records_written"] == 3
    assert result["leaks"] == []
    assert result["isolation_preserved"] is True

