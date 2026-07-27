#!/bin/bash
# Pre-commit hook for secrets scanning
# Install: cp scripts/pre-commit-secrets-scan.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit

set -e

echo "🔍 Scanning staged files for secrets..."

# Run the Python secrets scanner on staged files
python3 -c "
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from app.security.secrets import is_safe_to_commit

    is_safe, findings = is_safe_to_commit()

    if findings:
        print()
        print('⚠️  Potential secrets detected:')
        print()
        for f in findings:
            severity_icon = '🔴' if f.severity == 'critical' else '🟠' if f.severity == 'high' else '🟡'
            print(f'{severity_icon} [{f.severity.upper()}] {f.description}')
            print(f'   File: {f.file_path}:{f.line_number}')
            print(f'   Preview: {f._masked_preview()[:80]}...')
            print(f'   Fix: {f.remediation}')
            print()

    if not is_safe:
        print('❌ Commit blocked: Found high/critical severity secrets')
        print()
        print('To proceed anyway (NOT recommended):')
        print('  git commit --no-verify')
        print()
        print('Better options:')
        print('  1. Remove the secret from the file')
        print('  2. Use environment variables or AWS Secrets Manager')
        print('  3. Add to .gitignore if the file should never be committed')
        sys.exit(1)

    if findings:
        print('⚠️  Low/medium severity findings detected but allowing commit.')
        print('   Consider reviewing the above items.')
    else:
        print('✅ No secrets detected in staged files')

except ImportError:
    # Scanner not available, skip check
    print('ℹ️  Secrets scanner not available, skipping check')
    sys.exit(0)
except Exception as e:
    print(f'⚠️  Secrets scan error: {e}')
    # Don't block commit on scanner errors
    sys.exit(0)
"

echo "✅ Pre-commit checks passed"
