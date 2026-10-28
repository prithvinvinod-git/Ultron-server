/**
 * Memory panel — displays cross-layer memory recall for the current namespace.
 *
 * Reads from GET /memory and shows ranked, de-duplicated memories across
 * short-term, long-term, episodic, semantic, and project layers.
 *
 * Usage: <MemoryPanel /> — placed in the sidebar via the routes registry.
 */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { Search, Shield, Clock, Text, Folder, Brain, Eye } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";

import { memoryApi, MemoryRecord, MemoryLayer } from "@/lib/api";

interface MemoryRecordUI {
    id: string;
    namespace: string;
    kind: string;
    slug: string;
    layer: string;
    content: string;
    importance: number;
    score: number | null;
    created_at: string;
}

const DEFAULT_NAMESPACE = "user";
const LAYER_OPTIONS: Array<{ value: MemoryLayer; label: string }> = [
    { value: "long_term", label: "Long-term" },
    { value: "episodic", label: "Episodic" },
    { value: "short_term", label: "Short-term" },
    { value: "semantic", label: "Semantic" },
    { value: "project", label: "Project" },
];

export function MemoryPanel() {
    const [query, setQuery] = useState("");
    const [selectedLayer, setSelectedLayer] = useState<MemoryLayer | undefined>("long_term");
    const [topK, setTopK] = useState(8);
    const [loading, setLoading] = useState(false);
    const [records, setRecords] = useState<MemoryRecordUI[]>([]);
    const [error, setError] = useState<string | null>(null);

    const { data, isLoading } = useQuery<MemoryRecord[], Error>({
        queryKey: ["memory", DEFAULT_NAMESPACE, query, selectedLayer, topK],
        queryFn: () => memoryApi.recall(DEFAULT_NAMESPACE, query, topK, selectedLayer ? [selectedLayer] : undefined),
        enabled: true,
        refetchInterval: 10000,
    });

    const handleSearch = (e: React.ChangeEvent<HTMLInputElement>) => {
        setQuery(e.target.value);
        setRecords([]);
    };

    const handleRecall = async () => {
        setLoading(true);
        setError(null);
        try {
            const result = await memoryApi.recall(DEFAULT_NAMESPACE, query, topK, selectedLayer ? [selectedLayer] : undefined);
            setRecords(result.map((record) => ({
                id: record.id,
                namespace: record.namespace,
                kind: record.kind,
                slug: record.slug || "",
                layer: record.layer || "long_term",
                content: record.content || "",
                importance: record.importance ?? 0,
                score: record.score ?? null,
                created_at: record.created_at || new Date().toISOString(),
            })));
        } catch (err: any) {
            setError(err.message ?? "Memory recall failed");
            toast.error(err.message ?? "Failed to recall memories");
        } finally {
            setLoading(false);
        }
    };

    // Update records from query observer
    if (data && data.length > 0) {
        setRecords(
            data.map((record) => ({
                id: record.id,
                namespace: record.namespace,
                kind: record.kind,
                slug: record.slug || "",
                layer: record.layer || "long_term",
                content: record.content || "",
                importance: record.importance ?? 0,
                score: record.score ?? null,
                created_at: record.created_at || new Date().toISOString(),
            }))
        );
    }

    return (
        <section className="hud-panel">
            <div className="panel-heading">
                <h2>MEMORY · {DEFAULT_NAMESPACE}</h2>
                <Eye size={13} />
                <MemorySearchBar
                    query={query}
                    onQueryChange={handleSearch}
                    onRecall={handleRecall}
                    topK={topK}
                    setTopK={setTopK}
                    selectedLayer={selectedLayer}
                    setSelectedLayer={setSelectedLayer}
                    loading={loading}
                />
            </div>

            {error ? (
                <div className="panel-empty">{error}</div>
            ) : null}

            {loading ? (
                <div className="panel-empty">Loading...</div>
            ) : records.length === 0 ? (
                <div className="panel-empty">
                    <p>No memories found</p>
                    <p className="text-xs text-sidebar-foreground/60">
                        Try adjusting the search query or layer selection
                    </p>
                </div>
            ) : (
                <MemoryRecordList records={records} />
            )}
        </section>
    );
}

function MemorySearchBar({
    query,
    onQueryChange,
    onRecall,
    topK,
    setTopK,
    selectedLayer,
    setSelectedLayer,
    loading,
}: {
    query: string;
    onQueryChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
    onRecall: () => void;
    topK: number;
    setTopK: (value: number) => void;
    selectedLayer: MemoryLayer | undefined;
    setSelectedLayer: (layer: MemoryLayer | undefined) => void;
    loading: boolean;
}) {
    return (
        <div className="memory-search mb-4">
            <Input
                placeholder="Search memories..."
                value={query}
                onChange={onQueryChange}
                className="w-full"
                aria-label="Memory search"
            />
            <div className="flex gap-2 mt-2">
                <Select
                    value={selectedLayer}
                    onValueChange={(value) => setSelectedLayer(value as MemoryLayer)}
                >
                    <SelectTrigger>
                        <SelectValue placeholder="Select layer" />
                        <SelectContent>
                            {LAYER_OPTIONS.map((opt) => (
                                <SelectItem key={opt.value} value={opt.value}>
                                    {opt.label}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </SelectTrigger>
                </Select>

                <Select
                    value={topK.toString()}
                    onValueChange={(value) => setTopK(Number(value))}
                >
                    <SelectTrigger>
                        <SelectValue placeholder="Top K" />
                        <SelectContent>
                            <SelectItem value="3">3</SelectItem>
                            <SelectItem value="5">5</SelectItem>
                            <SelectItem value="8">8</SelectItem>
                            <SelectItem value="12">12</SelectItem>
                            <SelectItem value="20">20</SelectItem>
                        </SelectContent>
                    </SelectTrigger>
                </Select>

                <Button
                    variant="secondary"
                    size="sm"
                    onClick={onRecall}
                    disabled={loading}
                >
                    {loading ? "Searching..." : "Recall"}
                </Button>
            </div>
        </div>
    );
}

function MemoryRecordList({ records }: { records: MemoryRecordUI[] }) {
    return (
        <div className="memory-list">
            {records.map((record) => (
                <div
                    key={record.id}
                    className={cn(
                        "memory-row p-3 rounded-md border border-sidebar-border hover:border-sidebar-accent transition-colors",
                        record.score !== null && record.score > 0.7 && "border-sidebar-accent"
                    )}
                >
                    <div className="flex items-start gap-3">
                        <div className="memory-layer-badge w-3 h-3 rounded-full bg-sidebar-accent" />
                        <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-1">
                                <span className="memory-kind-tag text-xs font-medium text-sidebar-foreground/60">
                                    {record.kind}
                                </span>
                                <span className="memory-layer text-xs text-sidebar-foreground/50">
                                    {formatLayer(record.layer)}
                                </span>
                            </div>
                            <p className="memory-content text-sm line-clamp-3">{record.content}</p>
                            <div className="memory-meta text-xs">
                                <span>
                                    {record.importance > 0 && (
                                        <span className="importance-score">
                                            ⭐ {record.importance.toFixed(1)}
                                        </span>
                                    )}
                                </span>
                                <span className="ml-2 text-sidebar-foreground/50">
                                    {new Date(record.created_at).toLocaleDateString()}
                                </span>
                            </div>
                        </div>
                    </div>
                </div>
            ))}
        </div>
    );
}

function formatLayer(layer: string): string {
    const map: Record<string, string> = {
        long_term: "Long-term",
        episodic: "Episodic",
        short_term: "Short-term",
        semantic: "Semantic",
        project: "Project",
    };
    return map[layer] || layer;
}