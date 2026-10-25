/**
 * Models panel (T066, §20/§49).
 *
 * Shows what models this deployment can actually run by querying GET /models.
 * Groups by provider and highlights whether each is local or cloud, tool-capable,
 * and streaming-capable. The default/fallback chain from settings is also shown
 * so the operator can see what the router will pick absent an override.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Check, Database } from "lucide-react";

import { modelsApi } from "@/lib/api";
import type { ModelCatalog, ModelOption, ModelProviderInfo } from "@/lib/api";

export function ModelsPanel() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["models", "catalog"],
    queryFn: modelsApi.catalog,
    refetchOnWindowFocus: false,
  });

  if (error) {
    return (
      <section className="hud-panel">
        <div className="panel-heading">
          <h2>MODELS</h2>
          <Database size={13} />
        </div>
        <div className="panel-empty">model catalog unavailable</div>
      </section>
    );
  }

  if (isLoading && !data) {
    return (
      <section className="hud-panel">
        <div className="panel-heading">
          <h2>MODELS</h2>
          <Database size={13} />
        </div>
        <div className="panel-empty">loading…</div>
      </section>
    );
  }

  const { providers, models } = data ?? { providers: [], models: [] };

  // Group models by provider for cleaner display
  const modelsByProvider = useMemo(() => {
    const map = new Map<string, ModelOption[]>();
    for (const model of models) {
      const list = map.get(model.provider) ?? [];
      list.push(model);
      map.set(model.provider, list);
    }
    return map;
  }, [models]);

  return (
    <section className="hud-panel">
      <div className="panel-heading">
        <h2>MODELS</h2>
        <Database size={13} />
      </div>

      {providers.length === 0 ? (
        <div className="panel-empty">no model providers configured</div>
      ) : (
        <>
          <div className="model-providers">
            <div className="model-section-title">CONFIGURED PROVIDERS</div>
            {providers.map((prov) => (
              <div key={prov.name} className="provider-row">
                <span className={`provider-dot${prov.local ? "" : " cloud"}`} />
                <div>
                  <strong>{prov.name}</strong>
                  <small>
                    {prov.supports_tools ? "tools" : "no-tools"} ·
                    {prov.supports_streaming ? "stream" : "no-stream"} ·
                    {prov.configured ? "configured" : "not configured"}
                  </small>
                </div>
                <span className="provider-check">{prov.configured && <Check size={10} />}</span>
              </div>
            ))}
          </div>

          {models.length > 0 && (
            <>
              <div className="model-section-title">AVAILABLE MODELS</div>
              <div className="model-list">
                {Array.from(modelsByProvider.entries()).map(([provider, list]) => (
                  <div key={provider} className="provider-models">
                    <div className="provider-label">
                      <strong>{provider}</strong>
                    </div>
                    <div className="model-entries">
                      {list.map((model) => (
                        <div key={model.id} className="model-row">
                          <span className="model-dot" />
                          <div>
                            <strong>{model.label}</strong>
                            <small>
                              {model.provider} · {model.model} ·
                              {model.reason === "default" ? "default" : model.reason}
                            </small>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </>
      )}

      <div className="panel-foot">
        <span>{providers.length} providers</span>
        <span>{models.length} models</span>
      </div>
    </section>
  );
}