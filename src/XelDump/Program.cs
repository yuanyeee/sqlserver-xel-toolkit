using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.SqlServer.XEvent.XELite;

namespace XelDump;

public static class Program
{
    private const int DefaultMaxSampleEvents = 500;

    public static int Main(string[] args)
    {
        try
        {
            if (args.Length == 0 || args.Contains("-h") || args.Contains("--help"))
            {
                PrintHelp();
                return 0;
            }

            var inputPaths = new List<string>();
            string outputDir = Path.Combine(Directory.GetCurrentDirectory(), "output");
            int maxSampleEvents = DefaultMaxSampleEvents;
            string? exportJsonlPath = null;
            int exportLimit = 0; // 0 = unlimited
            string? filterEvent = null;

            for (int i = 0; i < args.Length; i++)
            {
                var a = args[i];
                if (a == "-o" || a == "--out")
                {
                    outputDir = args[++i];
                }
                else if (a == "--max")
                {
                    maxSampleEvents = int.Parse(args[++i]);
                }
                else if (a == "--export-jsonl")
                {
                    exportJsonlPath = args[++i];
                }
                else if (a == "--export-limit")
                {
                    exportLimit = int.Parse(args[++i]);
                }
                else if (a == "--filter")
                {
                    filterEvent = args[++i];
                }
                else
                {
                    inputPaths.Add(a);
                }
            }

            if (inputPaths.Count == 0)
            {
                Console.Error.WriteLine("No input .xel path provided.");
                return 2;
            }

            Directory.CreateDirectory(outputDir);

            var expanded = ExpandInputs(inputPaths);
            if (expanded.Count == 0)
            {
                Console.Error.WriteLine("No .xel files matched.");
                return 2;
            }

            Console.WriteLine($"Found {expanded.Count} xel file(s). Reading...");

            // Optional: export raw events as JSONL (may be large)
            if (!string.IsNullOrWhiteSpace(exportJsonlPath))
            {
                var exportPath = Path.GetFullPath(ExpandTilde(exportJsonlPath));
                Directory.CreateDirectory(Path.GetDirectoryName(exportPath) ?? outputDir);
                try
                {
                    ExportEventsJsonl(expanded, exportPath, filterEvent, exportLimit);
                }
                catch (AggregateException ae) when (ae.InnerException is OperationCanceledException)
                {
                    // Reached limit
                }
                catch (OperationCanceledException)
                {
                    // Reached limit
                }
                Console.WriteLine($"Exported JSONL: {exportPath}");
            }

            var summary = ScanXelFiles(expanded, maxSampleEvents);

            var baseName = SafeStem(expanded[0]);
            if (expanded.Count > 1) baseName = $"{baseName}_plus{expanded.Count - 1}";
            var ts = DateTime.Now.ToString("yyyyMMdd_HHmmss");
            var prefix = $"{baseName}_{ts}";

            var jsonPath = Path.Combine(outputDir, $"{prefix}_xel_summary.json");
            var mdPath = Path.Combine(outputDir, $"{prefix}_xel_summary.md");

            File.WriteAllText(jsonPath, JsonSerializer.Serialize(summary, new JsonSerializerOptions { WriteIndented = true }));
            File.WriteAllText(mdPath, RenderMarkdown(summary, expanded));

            Console.WriteLine($"Wrote: {jsonPath}");
            Console.WriteLine($"Wrote: {mdPath}");

            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex);
            return 1;
        }
    }

    private static void PrintHelp()
    {
        Console.WriteLine(@"sqlserver-xel-toolkit / XelDump

Usage:
  dotnet run --project src/XelDump -- <path|glob> [more paths...] [-o outputDir] [--max N] [--export-jsonl file] [--filter EventName] [--export-limit N]

Examples:
  dotnet run --project src/XelDump -- ~/Downloads/deadlock*.xel -o ./output
  dotnet run --project src/XelDump -- ~/Downloads/Slow_Queries_*.xel --max 2000
  dotnet run --project src/XelDump -- ~/Downloads/deadlock*.xel --export-jsonl ./output/deadlock.jsonl --filter xml_deadlock_report

Notes:
  - This command DOES NOT upload .xel anywhere. It reads locally and writes summary files.
  - Output includes a JSON summary and a Markdown summary.
");
    }

    private static List<string> ExpandInputs(List<string> inputs)
    {
        var result = new List<string>();

        foreach (var raw in inputs)
        {
            var path = ExpandTilde(raw);

            // If the user passed a directory, include all *.xel under it.
            if (Directory.Exists(path))
            {
                result.AddRange(Directory.GetFiles(path, "*.xel"));
                continue;
            }

            // If the user passed a glob (simple * only), expand it.
            if (path.Contains('*'))
            {
                var dir = Path.GetDirectoryName(path);
                if (string.IsNullOrEmpty(dir)) dir = Directory.GetCurrentDirectory();
                var pattern = Path.GetFileName(path);
                if (Directory.Exists(dir))
                    result.AddRange(Directory.GetFiles(dir, pattern));
                continue;
            }

            if (File.Exists(path)) result.Add(path);
        }

        return result
            .Select(Path.GetFullPath)
            .Where(p => p.EndsWith(".xel", StringComparison.OrdinalIgnoreCase))
            .Distinct()
            .OrderBy(p => p)
            .ToList();
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

    private static string SafeStem(string path)
    {
        var name = Path.GetFileName(path);
        var stem = Path.GetFileNameWithoutExtension(name);
        var sb = new StringBuilder();
        foreach (var ch in stem)
        {
            if (char.IsLetterOrDigit(ch) || ch == '.' || ch == '_' || ch == '-') sb.Append(ch);
            else sb.Append('_');
        }
        var s = sb.ToString().Trim(' ', '.', '_', '-');
        return string.IsNullOrEmpty(s) ? "output" : s;
    }

    private sealed record XelSummary(
        int MaxSampleEvents,
        int TotalEventsRead,
        Dictionary<string, long> EventCounts,
        Dictionary<string, List<string>> SampleFieldsByEvent
    );

    private static XelSummary ScanXelFiles(List<string> xelFiles, int maxSampleEvents)
    {
        var counts = new Dictionary<string, long>(StringComparer.OrdinalIgnoreCase);
        var fields = new Dictionary<string, HashSet<string>>(StringComparer.OrdinalIgnoreCase);
        int totalRead = 0;

        // XELite reads one file at a time; we chain them.
        foreach (var file in xelFiles)
        {
            Console.WriteLine($"Reading: {file}");
            var streamer = new XEFileEventStreamer(file);

            streamer.ReadEventStream(
                () => Task.CompletedTask,
                xevent =>
                {
                    totalRead++;
                    var name = xevent.Name ?? "(unknown)";
                    if (!counts.TryGetValue(name, out var c)) c = 0;
                    counts[name] = c + 1;

                    if (totalRead <= maxSampleEvents)
                    {
                        if (!fields.TryGetValue(name, out var set))
                        {
                            set = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                            fields[name] = set;
                        }

                        foreach (var kv in xevent.Fields)
                        {
                            set.Add(kv.Key);
                        }

                        foreach (var kv in xevent.Actions)
                        {
                            set.Add($"action:{kv.Key}");
                        }
                    }

                    return Task.CompletedTask;
                },
                CancellationToken.None
            ).Wait();
        }

        var sampleFields = fields
            .ToDictionary(
                kv => kv.Key,
                kv => kv.Value.OrderBy(x => x).ToList(),
                StringComparer.OrdinalIgnoreCase
            );

        return new XelSummary(
            MaxSampleEvents: maxSampleEvents,
            TotalEventsRead: totalRead,
            EventCounts: counts.OrderByDescending(kv => kv.Value).ToDictionary(kv => kv.Key, kv => kv.Value),
            SampleFieldsByEvent: sampleFields
        );
    }

    private static void ExportEventsJsonl(List<string> xelFiles, string outPath, string? filterEvent, int limit)
    {
        int written = 0;
        using var fs = new FileStream(outPath, FileMode.Create, FileAccess.Write, FileShare.Read);
        using var sw = new StreamWriter(fs, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));

        foreach (var file in xelFiles)
        {
            Console.WriteLine($"Exporting events from: {file}");
            var streamer = new XEFileEventStreamer(file);

            streamer.ReadEventStream(
                () => Task.CompletedTask,
                xevent =>
                {
                    var name = xevent.Name ?? "(unknown)";
                    if (!string.IsNullOrWhiteSpace(filterEvent) && !name.Equals(filterEvent, StringComparison.OrdinalIgnoreCase))
                        return Task.CompletedTask;

                    var record = new Dictionary<string, object?>
                    {
                        ["name"] = name,
                        ["timestamp"] = xevent.Timestamp,
                        ["fields"] = xevent.Fields,
                        ["actions"] = xevent.Actions,
                    };

                    sw.WriteLine(JsonSerializer.Serialize(record));
                    written++;

                    if (limit > 0 && written >= limit)
                        throw new OperationCanceledException("Reached export limit");

                    return Task.CompletedTask;
                },
                CancellationToken.None
            ).Wait();
        }
    }

    private static string RenderMarkdown(XelSummary summary, List<string> xelFiles)
    {
        var sb = new StringBuilder();
        sb.AppendLine("# XEL Summary");
        sb.AppendLine();
        sb.AppendLine($"- Created: {DateTime.Now:yyyy-MM-dd HH:mm:ss}");
        sb.AppendLine($"- XEL files: {xelFiles.Count}");
        sb.AppendLine($"- Total events read: {summary.TotalEventsRead}");
        sb.AppendLine($"- Sampled fields from first N events: {summary.MaxSampleEvents}");
        sb.AppendLine();

        sb.AppendLine("## Input files");
        foreach (var f in xelFiles) sb.AppendLine($"- {f}");
        sb.AppendLine();

        sb.AppendLine("## Event counts");
        sb.AppendLine();
        sb.AppendLine("EventName | Count");
        sb.AppendLine("---|---:");
        foreach (var kv in summary.EventCounts)
        {
            sb.AppendLine($"{kv.Key} | {kv.Value}");
        }
        sb.AppendLine();

        sb.AppendLine("## Sample fields (first N events)");
        sb.AppendLine();
        foreach (var ev in summary.SampleFieldsByEvent.OrderBy(k => k.Key))
        {
            sb.AppendLine($"### {ev.Key}");
            if (ev.Value.Count == 0)
            {
                sb.AppendLine("- (no fields captured)");
            }
            else
            {
                foreach (var f in ev.Value.Take(80))
                    sb.AppendLine($"- `{f}`");
                if (ev.Value.Count > 80) sb.AppendLine($"- ... ({ev.Value.Count - 80} more)");
            }
            sb.AppendLine();
        }

        return sb.ToString();
    }
}
