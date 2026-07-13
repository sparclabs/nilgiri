import { AllCommunityModule, ModuleRegistry, colorSchemeDark, createGrid, themeQuartz } from 'ag-grid-community';
import type { ColDef, ColGroupDef, GridApi } from 'ag-grid-community';

ModuleRegistry.registerModules([AllCommunityModule]);

interface GlobalMetrics {
  avg_flags_captured_at_3: number;
  avg_milestones_completed_at_3: number;
  pass_at_3: number;
}

interface MilestoneMetric {
  pass_at_3: number;
}

interface BudgetSlice {
  overall: GlobalMetrics;
  milestones: Record<string, MilestoneMetric>;
}

interface ModelRow {
  id: string;
  display_name: string;
  provider: string;
  by_token_budget: Record<string, BudgetSlice>;
  data_quality: { status: string; verified: boolean };
  notes?: string;
}

interface Milestone {
  id: string;
  name: string;
  flags: number;
}

interface ResultsData {
  runs_per_model: number;
  benchmark: { total_flags: number; total_milestones: number; milestones: Milestone[]; token_budgets: number[] };
  models: ModelRow[];
}

const dataEl = document.getElementById('nilgiri-results-data');
const results = JSON.parse(dataEl?.textContent ?? 'null') as ResultsData | null;

const gridDiv = document.getElementById('nilgiri-leaderboard-grid');
const tabsContainer = document.getElementById('milestone-tabs');
const captionEl = document.getElementById('milestone-caption');
const budgetSelect = document.getElementById('token-budget-select') as HTMLSelectElement | null;

if (results && gridDiv) {
  const data = results;
  const milestoneById = new Map(data.benchmark.milestones.map((m) => [m.id, m]));
  const maxBudget = Math.max(...data.benchmark.token_budgets);

  let currentTab = 'overall';
  let currentBudget = String(maxBudget);

  const theme = themeQuartz.withPart(colorSchemeDark).withParams({
    accentColor: '#22D3EE',
    backgroundColor: '#0b1220',
    foregroundColor: '#cbdbec',
    headerBackgroundColor: '#111a2c',
    borderColor: '#1f2b40',
  });

  const sliceFor = (row: ModelRow): BudgetSlice | undefined => row.by_token_budget[currentBudget];

  // One getter per sortable metric colId -- reused for both the column's
  // valueGetter (what the grid displays/sorts) and the winner-highlight
  // calc (which needs the same raw numbers outside of AG Grid's params).
  const metricGetters: Record<string, (row: ModelRow) => number> = {
    avgCTFs: (row) => sliceFor(row)?.overall.avg_flags_captured_at_3 ?? 0,
    avgMilestones: (row) => sliceFor(row)?.overall.avg_milestones_completed_at_3 ?? 0,
    passAt3: (row) => {
      const slice = sliceFor(row);
      if (!slice) return 0;
      return currentTab === 'overall' ? slice.overall.pass_at_3 : (slice.milestones[currentTab]?.pass_at_3 ?? 0);
    },
  };

  const baseColumns: ColDef<ModelRow>[] = [
    {
      field: 'display_name',
      headerName: 'Model',
      pinned: 'left',
      minWidth: 200,
      sortable: true,
      cellRenderer: (p: { value: string; data?: ModelRow }) => {
        const container = document.createElement('span');
        container.appendChild(document.createTextNode(p.value));
        if (p.data?.data_quality?.verified) {
          const check = document.createElement('span');
          check.title = 'Independently verified';
          check.style.color = '#22D3EE';
          check.textContent = ' ✓';
          container.appendChild(check);
        }
        return container;
      },
    },
    { field: 'provider', headerName: 'Provider', minWidth: 130, sortable: true },
  ];

  const globalColumns = (): ColGroupDef<ModelRow> => ({
    headerName: 'Global metrics',
    children: [
      {
        colId: 'avgCTFs',
        headerName: 'Avg CTFs@3',
        minWidth: 160,
        sort: 'desc',
        sortable: true,
        valueGetter: (p) => metricGetters.avgCTFs(p.data as ModelRow),
        valueFormatter: (p) => `${(p.value ?? 0).toFixed(2)} / ${data.benchmark.total_flags}`,
      },
      {
        colId: 'avgMilestones',
        headerName: 'Avg Milestones@3',
        minWidth: 180,
        sortable: true,
        valueGetter: (p) => metricGetters.avgMilestones(p.data as ModelRow),
        valueFormatter: (p) => `${(p.value ?? 0).toFixed(2)} / ${data.benchmark.total_milestones}`,
      },
    ],
  });

  const milestoneColumns = (tabId: string): ColGroupDef<ModelRow> => {
    const label = tabId === 'overall' ? 'Overall' : tabId;
    return {
      headerName: 'Milestone-dependent metrics',
      children: [
        {
          colId: 'passAt3',
          headerName: `Pass@3 (${label})`,
          minWidth: 230,
          sortable: true,
          valueGetter: (p) => metricGetters.passAt3(p.data as ModelRow),
          valueFormatter: (p) => `${Math.round((p.value ?? 0) * 100)}%`,
        },
      ],
    };
  };

  const buildColumnDefs = () => [...baseColumns, globalColumns(), milestoneColumns(currentTab)];

  const gridApi: GridApi<ModelRow> = createGrid(gridDiv, {
    theme,
    rowData: data.models,
    columnDefs: buildColumnDefs(),
    defaultColDef: { resizable: true, sortable: true },
    domLayout: 'autoHeight',
    onSortChanged: () => applyWinnerHighlight(),
  });

  function currentSortColId(): string {
    const sorted = gridApi.getColumnState().find((c) => c.sort);
    return sorted?.colId ?? 'avgCTFs';
  }

  function applyWinnerHighlight() {
    const colId = currentSortColId();
    const getter = metricGetters[colId];
    if (!getter) {
      gridApi.setGridOption('getRowStyle', () => undefined);
      gridApi.redrawRows();
      return;
    }
    const best = Math.max(...data.models.map(getter));
    gridApi.setGridOption('getRowStyle', (params) => {
      if (!params.data) return undefined;
      return Math.abs(getter(params.data) - best) < 1e-6 ? { fontWeight: 'bold' } : undefined;
    });
    gridApi.redrawRows();
  }
  applyWinnerHighlight();

  const updateCaption = (tabId: string) => {
    if (!captionEl) return;
    if (tabId === 'overall') {
      captionEl.textContent = `Overall combines all ${data.benchmark.total_flags} flags across all ${data.benchmark.total_milestones} milestones.`;
    } else {
      const m = milestoneById.get(tabId);
      captionEl.textContent = m ? `${tabId}: ${m.name} (${m.flags} flags).` : '';
    }
  };
  updateCaption(currentTab);

  tabsContainer?.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tabId = btn.dataset.tab;
      if (!tabId || btn.getAttribute('aria-selected') === 'true') return;

      tabsContainer.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach((b) => {
        const active = b === btn;
        b.setAttribute('aria-selected', String(active));
        b.classList.toggle('bg-primary', active);
        b.classList.toggle('text-white', active);
        b.classList.toggle('border-primary', active);
        b.classList.toggle('border-gray-300', !active);
        b.classList.toggle('dark:border-slate-600', !active);
        b.classList.toggle('text-muted', !active);
        // hover:* only makes sense on the inactive pill -- left on the active
        // one, `:hover` outranks `.text-white` in specificity and the label
        // goes invisible (cyan-on-cyan) whenever the pointer rests on it.
        b.classList.toggle('hover:border-primary', !active);
        b.classList.toggle('hover:text-primary', !active);
      });

      currentTab = tabId;
      gridApi.setGridOption('columnDefs', buildColumnDefs());
      updateCaption(tabId);
      applyWinnerHighlight();
    });
  });

  budgetSelect?.addEventListener('change', () => {
    currentBudget = budgetSelect.value;
    gridApi.setGridOption('columnDefs', buildColumnDefs());
    applyWinnerHighlight();
  });
}
