# Frontend UI/UX Redesign Design Document

## Meta

- Date: 2026-06-27
- Scope: High + Medium priority UI/UX improvements
- Approach: Component-level unification + partial refactor (gradual replacement, no page deprecation)
- Status: Design approved, ready for writing-plans

---

## 1. Design Principles & Overall Architecture

### 1.1 Principles

1. **Consistency**: unify layouts, lists, feedback, and interactions across pages.
2. **Clarity**: reduce information density on detail pages; progressive disclosure via tabs and panels.
3. **Responsiveness**: support 1366px, 1024px, and 768px breakpoints without horizontal overflow or unusable canvases.
4. **Accessibility**: all interactive elements must be keyboard reachable and screen-reader friendly.
5. **Minimal Intrusion**: introduce new components alongside existing ones; migrate pages incrementally.

### 1.2 Overall Architecture

```
AppShell
├── Sidebar
├── TopBar (global search, user menu, notifications only)
└── main
    └── PageContainer
        ├── PageHeaderV2 (title, breadcrumbs, actions, description)
        └── Page Content
            ├── DataList / DataTable
            ├── Tabs
            ├── Resizable Panels
            └── etc.
```

- Abandon `HeaderSlotContext` injection pattern.
- Page headers are rendered declaratively by each page using `PageHeaderV2`.
- `HeaderSlotContext` is kept but marked `@deprecated` with a runtime warning until all callers are migrated.

---

## 2. Layout Framework Refactor

### 2.1 Current Issues

- `HeaderSlotContext` causes title jumping and duplicate headers.
- Global `p-6` padding on `<main>` forces full-bleed pages to fight the layout.
- Scroll ownership is unclear: some pages scroll globally, some locally.

### 2.2 Components

#### `PageContainer`

```tsx
interface PageContainerProps {
  children: React.ReactNode;
  fullBleed?: boolean;      // default false
  className?: string;
  innerClassName?: string;
  scrollable?: boolean;     // default true
}
```

- Default: `px-6 py-6`, `overflow-auto`, fills remaining viewport height.
- `fullBleed`: removes horizontal padding for lists/tables to touch edges.
- `scrollable={false}`: fills height but does not scroll, used by `PlanEditPage`.

#### `PageHeaderV2`

```tsx
interface PageHeaderV2Props {
  title: React.ReactNode;
  breadcrumbs?: BreadcrumbItem[];
  actions?: React.ReactNode;
  secondaryActions?: React.ReactNode;
  description?: React.ReactNode;
  sticky?: boolean;
  className?: string;
}
```

- Renders title, breadcrumbs, actions, and description in one place.
- `sticky` mode uses `sticky top-0 z-10` inside a scrolling container.
- Responsive: actions wrap on small screens; secondary actions collapse into a menu.

### 2.3 `AppShell` Changes

```tsx
<div className="flex h-screen">
  <Sidebar />
  <div className="flex flex-col flex-1 min-w-0">
    <TopBar />
    <main className="flex-1 min-h-0 overflow-hidden">
      <Outlet />
    </main>
  </div>
</div>
```

- `TopBar` height fixed at `h-14`, no page title.
- `<main>` is `overflow-hidden`; `PageContainer` decides scroll behavior.
- Remove global `<PageHeader />` rendering.

### 2.4 Migration Strategy

| Page | Before | After |
|------|--------|-------|
| PlanListPage | HeaderSlotContext + card list | PageHeaderV2 + PageContainer fullBleed + DataList |
| PlanRunListPage | HeaderSlotContext + card list | PageHeaderV2 + PageContainer fullBleed + DataList |
| ResultsPage | Local title + table | PageHeaderV2 + PageContainer fullBleed + DataTable |
| PlanRunDetailPage | HeaderSlotContext + long scroll | PageHeaderV2 + PageContainer + Tabs |
| PlanEditPage | Duplicate titles | PageHeaderV2 + PageContainer scrollable={false} |
| Other old pages | HeaderSlotContext | Keep compatible, migrate later |

### 2.5 Acceptance

- No duplicate titles on migrated pages.
- Full-bleed lists/tables touch left/right edges.
- `PlanEditPage` has no full-page scrollbar; panels scroll independently.
- TypeScript compiles; old pages still work.

---

## 3. Unified List / Table Components

### 3.1 Current Issues

- `PlanListPage`: hover-only action buttons, not touch/keyboard friendly.
- `PlanRunListPage`: whole-row click conflicts with inline buttons.
- `ResultsPage`: table squeezed by padding, no row actions menu.

### 3.2 Components

#### `DataList<T>`

```tsx
interface DataListProps<T> {
  items: T[];
  isLoading?: boolean;
  error?: Error | null;
  emptyState?: React.ReactNode;
  renderItem: (item: T, ctx: { isSelected: boolean; toggleSelect: () => void }) => React.ReactNode;
  keyExtractor: (item: T) => string;
  selection?: 'none' | 'single' | 'multiple';
  selectedKeys?: Set<string>;
  onSelectionChange?: (keys: Set<string>) => void;
  header?: React.ReactNode;
  footer?: React.ReactNode;
  itemClassName?: string;
}
```

Default item wrapper `DataListItem`:

```tsx
interface DataListItemProps {
  children: React.ReactNode;
  actions?: React.ReactNode;       // always visible
  moreActions?: { label: string; onClick: () => void; destructive?: boolean }[];
  onNavigate?: () => void;
  selected?: boolean;
  onSelect?: () => void;
}
```

- No hover-only buttons.
- Left checkbox (when selection enabled), main content clickable, right actions always visible, overflow in `...` menu.

#### `DataTable<T>`

```tsx
interface DataTableProps<T> {
  data: T[];
  columns: ColumnDef<T>[];
  isLoading?: boolean;
  error?: Error | null;
  emptyState?: React.ReactNode;
  selection?: 'none' | 'multiple';
  selectedKeys?: Set<string>;
  onSelectionChange?: (keys: Set<string>) => void;
  header?: React.ReactNode;
  footer?: React.ReactNode;
  rowActions?: (row: T) => { label: string; onClick: () => void; destructive?: boolean }[];
}
```

- Built on `@tanstack/react-table`.
- Row actions in a trailing `...` menu.
- Column widths via `meta.width`, `meta.minWidth`, `meta.maxWidth`.

### 3.3 Shared Subcomponents

- `DataToolbar`: search, filters, sort, view toggle.
- `DataBulkActionsBar`: appears when items selected.
- `DataPagination`: page numbers + page size.
- `DataEmptyState`: icon + title + description + optional action.
- `DataErrorState`: message + retry button.
- `DataSkeleton`: list/table skeleton.

### 3.4 Page Applications

- `PlanListPage`: `DataList` with Grid/List view toggle. Grid uses `minmax(320px, 1fr)`.
- `PlanRunListPage`: `DataList` default List view.
- `ResultsPage`: `DataTable` with row actions menu.

### 3.5 Accessibility

- All row actions keyboard focusable.
- Dropdown menus support Enter/Space/Arrow keys.
- List items have descriptive `aria-label`.
- Checkboxes use `aria-checked`.

### 3.6 Acceptance

- No hover-only action buttons.
- Empty states centered in content area.
- Tables scroll horizontally without header jitter.
- Keyboard navigation works for list items and menus.

---

## 4. PlanRun Detail Page Reorganization

### 4.1 Current Issues

- All information stacked in one long scroll.
- Real-time watcher signals cause whole-page jitter.
- Key actions scattered across cards.

### 4.2 Structure

```tsx
<PageContainer>
  <PageHeaderV2
    title={planRun.planName}
    breadcrumbs={[{ label: 'Plan Runs', href: '/runs' }, { label: planRun.id }]}
    actions={
      <>
        <Button variant="outline" onClick={cancelRun} disabled={!canCancel}>Cancel</Button>
        <Button onClick={retryRun} disabled={!canRetry}>Retry</Button>
        <Button variant="outline" onClick={downloadReport}>Download Report</Button>
      </>
    }
    description={<PlanRunMeta planRun={planRun} />}
  />

  <RunStatusBanner planRun={planRun} />

  <Tabs defaultValue="overview">
    <TabsList>
      <TabsTrigger value="overview">Overview</TabsTrigger>
      <TabsTrigger value="devices">Devices</TabsTrigger>
      <TabsTrigger value="artifacts">Artifacts</TabsTrigger>
      <TabsTrigger value="logs">Logs & Signals</TabsTrigger>
      <TabsTrigger value="timeline">Timeline</TabsTrigger>
    </TabsList>

    <TabsContent value="overview"><RunOverviewTab /></TabsContent>
    <TabsContent value="devices"><RunDevicesTab /></TabsContent>
    <TabsContent value="artifacts"><RunArtifactsTab /></TabsContent>
    <TabsContent value="logs"><RunLogsTab /></TabsContent>
    <TabsContent value="timeline"><RunTimelineTab /></TabsContent>
  </Tabs>
</PageContainer>
```

### 4.3 Tab Contents

- **Overview**: status hero, progress, timing, quick stats, recent events.
- **Devices**: `DataTable` of devices with status filter and per-device actions.
- **Artifacts**: `DataList` or `DataTable` of artifacts with download links.
- **Logs & Signals**: two inner tabs — Logs and Watcher Signals. Watcher Signals shows last refresh time and `AnomalyDashboard` summary.
- **Timeline**: vertical timeline of key PlanRun events.

### 4.4 Components

New components under `frontend/src/pages/execution/PlanRunDetailPage/`:

| Component | Responsibility |
|-----------|----------------|
| `PlanRunDetailPage.tsx` | Route entry, header, tabs shell |
| `RunStatusBanner.tsx` | Status banner |
| `RunOverviewTab.tsx` | Overview tab |
| `RunDevicesTab.tsx` | Devices tab |
| `RunArtifactsTab.tsx` | Artifacts tab |
| `RunLogsTab.tsx` | Logs inner tab |
| `RunSignalsTab.tsx` | Watcher Signals inner tab |
| `RunTimelineTab.tsx` | Timeline tab |
| `PlanRunMeta.tsx` | Header description metadata |

### 4.5 Data Fetching

- Parent fetches base PlanRun info.
- Each tab fetches its own data via `react-query` with `enabled`.
- Watcher Signals has independent `refetchInterval`.
- Cancel/retry invalidates relevant queries on success.

### 4.6 Acceptance

- First screen shows only status, stats, and tabs.
- Tabs scroll independently.
- Watcher Signals refresh does not rerender other tabs.
- No page-load regression.

---

## 5. PlanEditPage Responsive Three-Column Redesign

### 5.1 Current Issues

- Fixed three-column layout squeezes canvas below 1366px.
- Duplicate titles.
- Unclear scroll behavior.
- Side panels cannot collapse.

### 5.2 Layout

```tsx
<PageContainer scrollable={false}>
  <PageHeaderV2
    title={isNew ? 'Create Plan' : `Edit: ${plan.name}`}
    breadcrumbs={[{ label: 'Plans', href: '/plans' }, { label: isNew ? 'Create' : 'Edit' }]}
    actions={
      <>
        <Button variant="outline" onClick={saveDraft}>Save Draft</Button>
        <Button onClick={publishPlan}>Publish</Button>
      </>
    }
  />

  <Toolbar />

  <ResizablePanelGroup direction="horizontal" className="flex-1">
    <ResizablePanel defaultSize={20} minSize={15} maxSize={30} collapsible>
      <StepLibraryPanel />
    </ResizablePanel>

    <ResizableHandle withHandle />

    <ResizablePanel defaultSize={60} minSize={40}>
      <CanvasPanel />
    </ResizablePanel>

    <ResizableHandle withHandle />

    <ResizablePanel defaultSize={20} minSize={15} maxSize={30} collapsible>
      <PropertiesPanel />
    </ResizablePanel>
  </ResizablePanelGroup>
</PageContainer>
```

### 5.3 Responsive Breakpoints

| Breakpoint | Behavior |
|------------|----------|
| `>= 1280px` | Three panels expanded, default 20/60/20 |
| `1024px - 1279px` | Three panels, default 22/56/22, right panel collapsible |
| `768px - 1023px` | Side panels collapsed, toolbar buttons open drawers |
| `< 768px` | Side panels as full-screen drawers |

### 5.4 Panels

- **StepLibraryPanel**: search, categorized step list, drag/click to add.
- **CanvasPanel**: toolbar (undo/redo, zoom, fit, layout toggle), canvas area, mini-map/zoom indicator.
- **PropertiesPanel**: empty state when no step selected; form when selected, internal scroll.

### 5.5 Toolbar

Located between header and panel group:

```tsx
<div className="flex items-center justify-between px-4 py-2 border-b">
  <div className="flex gap-2">
    <Button variant="ghost" size="sm" onClick={toggleLeftPanel}><PanelLeftIcon /></Button>
    <Button variant="ghost" size="sm" onClick={toggleRightPanel}><PanelRightIcon /></Button>
  </div>
  <div className="flex gap-2">
    <Button variant="ghost" size="sm" onClick={undo}>Undo</Button>
    <Button variant="ghost" size="sm" onClick={redo}>Redo</Button>
    <ZoomControls />
    <LayoutToggle />
  </div>
</div>
```

### 5.6 State

- `leftPanelCollapsed`, `rightPanelCollapsed`: persisted in URL query or localStorage.
- Panel sizes: controlled or uncontrolled via `react-resizable-panels`.

### 5.7 Acceptance

- Canvas usable at 1366px.
- Side panels resizable and collapsible.
- No full-page scrollbar.
- No duplicate title.
- Undo/redo/zoom/layout toggle do not regress.

---

## 6. Toast, User Menu, and Other Medium-Priority Improvements

### 6.1 Toast System

Use `sonner` with a wrapper hook:

```tsx
const { toast } = useToast();

toast.success('Plan published');
toast.error({ title: 'Failed to cancel run', description: err.message });
toast.promise(cancelRun(runId), {
  loading: 'Cancelling run...',
  success: 'Run cancelled',
  error: (err) => ({ title: 'Cancel failed', description: err.message }),
});
```

- Position: top-right.
- Max 3 toasts; oldest removed first.
- Durations: success 3s, info 4s, warning 5s, error persistent.

### 6.2 User Menu

```tsx
<UserMenu
  user={user}
  items={[
    { label: 'Profile', href: '/profile' },
    { label: 'Settings', href: '/settings' },
    { type: 'divider' },
    { label: 'Logout', onClick: logout, destructive: true },
  ]}
/>
```

- Based on `DropdownMenu`.
- Full keyboard navigation, focus trap, `aria-expanded`.

### 6.3 Theme & Tokens

- Use shadcn CSS variables consistently.
- No hard-coded colors.
- Dark mode not required in this phase, but tokens must support future switch.

### 6.4 Loading / Empty / Error States

- `PageSkeleton` for initial page load.
- `DataSkeleton` for lists/tables.
- `DataEmptyState` and `DataErrorState` shared.
- Button loading via `Button` loading prop.

### 6.5 Forms

- Validation messages via `FormMessage` next to inputs.
- Global submission errors in `Alert` at form top.
- Focus ring: `ring-2 ring-ring ring-offset-2`.

### 6.6 Accessibility Baseline

- All icon buttons have `aria-label`.
- Modals use `Dialog` with focus trap and `aria-modal`.
- Page titles update via `useDocumentTitle`.

### 6.7 Acceptance

- Toast position/duration matches spec.
- User menu keyboard operable.
- Icon buttons have labels.
- Loading/empty/error states are unified.

---

## 7. Implementation Phases & Acceptance

### 7.1 Phase 1: Foundation Components & Layout Framework

**Tasks**:
1. Create `PageContainer`, `PageHeaderV2`.
2. Modify `AppShell`: remove global `PageHeader`, keep TopBar minimal.
3. Mark `HeaderSlotContext` `@deprecated`, add runtime warning.
4. Create `DataList`, `DataTable`, and shared data subcomponents.
5. Add dependencies: `@tanstack/react-table`, `react-resizable-panels`, `sonner`.

**Verification**:
- `tsc --noEmit` passes.
- New components previewable.
- Old pages still work.

### 7.2 Phase 2: List Pages Unification

**Tasks**:
1. Migrate `PlanListPage` to `PageHeaderV2 + PageContainer fullBleed + DataList`.
2. Migrate `PlanRunListPage` similarly.
3. Migrate `ResultsPage` to `PageHeaderV2 + PageContainer fullBleed + DataTable`.
4. Unify loading/empty/error states.

**Verification**:
- No hover-only buttons.
- Empty states centered.
- Horizontal table scroll works.
- Existing vitest tests pass (update as needed).

### 7.3 Phase 3: Detail & Edit Pages

**Tasks**:
1. Refactor `PlanRunDetailPage` into tabs + subcomponents.
2. Refactor `PlanEditPage` into responsive three-column layout.

**Verification**:
- Tabs scroll independently.
- Watcher Signals refresh isolated.
- `PlanEditPage` usable at 1366px.
- No duplicate titles.

### 7.4 Phase 4: Toast, User Menu, Accessibility

**Tasks**:
1. Integrate `sonner`, wrap `useToast`, replace ad-hoc toasts.
2. Refactor `UserMenu`.
3. Add/check `aria-label` on icon buttons.
4. Add `useDocumentTitle`.
5. Remove `HeaderSlotContext` after confirming no callers remain.

**Verification**:
- Toast spec met.
- User menu keyboard operable.
- `tsc --noEmit`, vitest, build all pass.
- No `HeaderSlotContext` references.

### 7.5 Global Acceptance Criteria

| Dimension | Criterion |
|-----------|-----------|
| Functionality | Core features preserved on all migrated pages |
| Layout | No full-page unexpected scroll, no duplicate titles, full-bleed works |
| Responsive | 1366px, 1024px, 768px without overlap or unusable areas |
| Interaction | Action buttons visible, keyboard reachable, menu keyboard operable |
| States | Unified loading/empty/error, Toast spec compliant |
| Performance | First paint not degraded, tab lazy-loading effective |
| Code | `tsc --noEmit` passes, vitest passes, `npm run build` passes |
| Accessibility | Icon buttons labeled, dialogs focus-trapped |

### 7.6 Estimated File Changes

#### New Components

- `frontend/src/components/layout/PageContainer.tsx`
- `frontend/src/components/layout/PageHeaderV2.tsx`
- `frontend/src/components/data/DataList.tsx`
- `frontend/src/components/data/DataTable.tsx`
- `frontend/src/components/data/DataToolbar.tsx`
- `frontend/src/components/data/DataEmptyState.tsx`
- `frontend/src/components/data/DataErrorState.tsx`
- `frontend/src/components/data/DataSkeleton.tsx`
- `frontend/src/components/data/DataPagination.tsx`
- `frontend/src/components/ui/Toaster.tsx`
- `frontend/src/hooks/useToast.ts`
- `frontend/src/hooks/useDocumentTitle.ts`

#### Modified Files

- `frontend/src/layouts/AppShell.tsx`
- `frontend/src/contexts/HeaderSlotContext.tsx`
- `frontend/src/pages/orchestration/PlanListPage.tsx`
- `frontend/src/pages/execution/PlanRunListPage.tsx`
- `frontend/src/pages/results/ResultsPage.tsx`
- `frontend/src/pages/execution/PlanRunDetailPage.tsx` + split subcomponents
- `frontend/src/pages/orchestration/PlanEditPage.tsx`

#### New Dependencies

- `@tanstack/react-table`
- `react-resizable-panels`
- `sonner`

### 7.7 Risks & Mitigation

| Risk | Mitigation |
|------|------------|
| `react-resizable-panels` incompatible with shadcn | Test in isolated branch before merging |
| Removing `HeaderSlotContext` breaks old pages | Delete only in Phase 4 after all callers migrated |
| Large visual changes confuse users | Keep Grid/List toggle, default to view closest to original |

---

## Next Step

Proceed to `writing-plans` skill to break the above phases into executable tasks.
