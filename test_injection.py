import subprocess
url = 'postgresql://user:pass@localhost/db" -c "SELECT 1'
cmd = ["pg_dump", "-d", url, "-F", "p"]
print("CMD:", cmd)
