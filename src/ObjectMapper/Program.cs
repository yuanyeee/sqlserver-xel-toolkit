using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using Microsoft.Data.SqlClient;

namespace ObjectMapper;

public static class Program
{
    private sealed record KeyItem(int database_id, int object_id, int? index_id);

    private sealed record MapItem(int database_id, int object_id, int? index_id, string? database_name, string? object_name, string? index_name);

    public static int Main(string[] args)
    {
        try
        {
            if (args.Contains("-h") || args.Contains("--help"))
            {
                PrintHelp();
                return 0;
            }

            string? inPath = null;
            string? outPath = null;

            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--in") inPath = args[++i];
                else if (args[i] == "--out") outPath = args[++i];
            }

            if (string.IsNullOrWhiteSpace(inPath) || string.IsNullOrWhiteSpace(outPath))
            {
                Console.Error.WriteLine("Missing --in/--out");
                PrintHelp();
                return 2;
            }

            var connStr = Environment.GetEnvironmentVariable("MSSQL_CONNSTR");
            if (string.IsNullOrWhiteSpace(connStr))
            {
                // Optional by design
                File.WriteAllText(outPath, "{}");
                return 0;
            }

            var json = File.ReadAllText(ExpandTilde(inPath));
            var keys = JsonSerializer.Deserialize<List<KeyItem>>(json) ?? new List<KeyItem>();
            keys = keys
                .Where(k => k.database_id > 0 && k.object_id > 0)
                .Distinct()
                .ToList();

            using var conn = new SqlConnection(connStr);
            conn.Open();

            // Map database_id -> name
            var dbIdToName = new Dictionary<int, string>();
            using (var cmd = new SqlCommand("SELECT database_id, name FROM sys.databases", conn))
            using (var r = cmd.ExecuteReader())
            {
                while (r.Read())
                {
                    dbIdToName[r.GetInt32(0)] = r.GetString(1);
                }
            }

            var result = new List<MapItem>();

            foreach (var grp in keys.GroupBy(k => k.database_id))
            {
                if (!dbIdToName.TryGetValue(grp.Key, out var dbName))
                    continue;

                // Switch context per DB for OBJECT_NAME etc.
                var safeDb = dbName.Replace("]", "]]" );
                using (var useCmd = new SqlCommand($"USE [{safeDb}]", conn))
                {
                    useCmd.ExecuteNonQuery();
                }

                // Build a lookup for objects
                var objectIds = grp.Select(k => k.object_id).Distinct().ToList();
                var objMap = new Dictionary<int, string>();

                // Chunk to avoid huge IN lists
                foreach (var chunk in Chunk(objectIds, 900))
                {
                    var inList = string.Join(",", chunk);
                    using var cmd = new SqlCommand($"SELECT object_id, name FROM sys.objects WHERE object_id IN ({inList})", conn);
                    using var rr = cmd.ExecuteReader();
                    while (rr.Read())
                        objMap[rr.GetInt32(0)] = rr.GetString(1);
                }

                // Index names (optional)
                var idxKeys = grp.Where(k => k.index_id.HasValue).ToList();
                var idxMap = new Dictionary<(int object_id, int index_id), string>();
                foreach (var chunk in Chunk(idxKeys, 800))
                {
                    // use values list
                    var values = string.Join(",", chunk.Select(k => $"({k.object_id},{k.index_id!.Value})"));
                    var sql = $@"
WITH t(object_id, index_id) AS (
    SELECT v.object_id, v.index_id
    FROM (VALUES {values}) AS v(object_id, index_id)
)
SELECT t.object_id, t.index_id, i.name
FROM t
LEFT JOIN sys.indexes i
  ON i.object_id = t.object_id AND i.index_id = t.index_id";
                    using var cmd = new SqlCommand(sql, conn);
                    using var rr = cmd.ExecuteReader();
                    while (rr.Read())
                    {
                        var oid = rr.GetInt32(0);
                        var iid = rr.GetInt32(1);
                        var name = rr.IsDBNull(2) ? null : rr.GetString(2);
                        if (name != null)
                            idxMap[(oid, iid)] = name;
                    }
                }

                foreach (var k in grp)
                {
                    objMap.TryGetValue(k.object_id, out var objName);
                    string? idxName = null;
                    if (k.index_id.HasValue)
                        idxMap.TryGetValue((k.object_id, k.index_id.Value), out idxName);

                    result.Add(new MapItem(k.database_id, k.object_id, k.index_id, dbName, objName, idxName));
                }
            }

            var outObj = result
                .GroupBy(x => $"{x.database_id}|{x.object_id}|{(x.index_id.HasValue ? x.index_id.Value.ToString() : "")}")
                .ToDictionary(
                    g => g.Key,
                    g => new { g.First().database_name, g.First().object_name, g.First().index_name }
                );

            Directory.CreateDirectory(Path.GetDirectoryName(ExpandTilde(outPath)) ?? Directory.GetCurrentDirectory());
            File.WriteAllText(ExpandTilde(outPath), JsonSerializer.Serialize(outObj));

            return 0;
        }
        catch (Exception ex)
        {
            // Optional by design: do not fail the overall pipeline.
            Console.Error.WriteLine($"[ObjectMapper] WARN: {ex.Message}");
            // Still write empty map
            try
            {
                var outPath = GetArg(args, "--out");
                if (!string.IsNullOrWhiteSpace(outPath))
                    File.WriteAllText(ExpandTilde(outPath), "{}");
            }
            catch { }
            return 0;
        }
    }

    private static string? GetArg(string[] args, string name)
    {
        for (int i = 0; i < args.Length - 1; i++)
            if (args[i] == name) return args[i + 1];
        return null;
    }

    private static IEnumerable<List<T>> Chunk<T>(IEnumerable<T> items, int size)
    {
        var bucket = new List<T>(size);
        foreach (var item in items)
        {
            bucket.Add(item);
            if (bucket.Count >= size)
            {
                yield return bucket;
                bucket = new List<T>(size);
            }
        }
        if (bucket.Count > 0) yield return bucket;
    }

    private static string ExpandTilde(string path)
    {
        if (string.IsNullOrWhiteSpace(path)) return path;
        if (path.StartsWith("~/"))
        {
            var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            return Path.Combine(home, path.Substring(2));
        }
        return path;
    }

    private static void PrintHelp()
    {
        Console.WriteLine(@"ObjectMapper

Maps (database_id, object_id, index_id) to names via SQL Server.
Reads MSSQL_CONNSTR from environment. If missing or errors, outputs empty mapping (no failure).

Usage:
  dotnet run --project src/ObjectMapper -- --in keys.json --out map.json

keys.json format:
  [{""database_id"":8,""object_id"":12345,""index_id"":1}, ...]
");
    }
}
