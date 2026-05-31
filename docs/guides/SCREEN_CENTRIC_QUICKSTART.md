# Screen-Centric Documentation: Quick Start Guide

This guide walks you through using ai-discovery's screen-centric documentation system to generate user-facing specs from menus and map them to backend systems.

## Overview

The screen-centric mode generates documentation organized by **user-facing screens** (what users interact with) rather than technical domains. Each screen spec includes:

- **What**: Screen purpose, user actions, system responses
- **Why**: Business rules, validations, permissions
- **Where**: Backend APIs, services, database tables
- **When**: Downstream effects, batch jobs, external interfaces

## Prerequisites

- ai-discovery installed (`pip install .` from repo root)
- Repository with menu definitions (JSON, YAML, TypeScript, or framework routing)
- Source code for backend mapping (Java/Spring, .NET/ASP.NET, Node.js/Express)

## Step 1: Detect Screens

Auto-detect menu structure and build screen definitions:

```bash
# Basic: detect screens only
discover detect-screens /path/to/repo --output ./data/screens

# Enhanced: detect screens + map to backend
discover detect-screens /path/to/repo --output ./data/screens --include-backend

# Without backend mapping
discover detect-screens /path/to/repo --no-backend
```

**What this does:**
1. Scans for menu definitions:
   - JSON/YAML files: `menu.json`, `navigation.yaml`, `routes.json`
   - TypeScript constants: `export const MENU = [...]`
   - Framework routing: Vue Router, React Router, Angular routing
2. Extracts screen definitions (title, path, roles)
3. Optionally maps to backend components (controllers, services, tables)
4. Computes source file hashes for drift detection

**Output files:**
- `screen_map.yaml` — All detected screens with backend mappings
- `menu_tree.json` — Hierarchical menu structure for navigation

### Example: Spring Boot Application

```bash
# Repository with src/config/menu.json
discover detect-screens ~/projects/myapp --output ./docs/screens --include-backend
```

Output includes:
```yaml
screens:
  - screen_id: customer-search
    menu_path: [Customers, Search, Customer Search]
    label: Search Customers
    path: /customers/search
    fe_component: src/pages/Customer/SearchPage.vue
    be_controllers:
      - CustomerController
    be_services:
      - CustomerService
    db_tables:
      - CUSTOMER
      - CUSTOMER_ADDRESS
    source_hashes:
      src/pages/Customer/SearchPage.vue: abc123...
      src/main/java/.../CustomerController.java: def456...
```

## Step 2: Verify Source Code Hasn't Changed (Drift Detection)

Check if screen specs are out of sync with source code:

```bash
# Check for drift between specs and source
discover verify-drift /path/to/repo --spec-dir ./docs/screens
```

**What this does:**
1. Reads `source_hashes` from spec frontmatter
2. Compares against current file hashes (SHA256)
3. Reports which files changed
4. Exits with code 0 (no drift) or 1 (drift detected)

**Output example:**
```
🔄 Drift Detection Report
═════════════════════════
Drifted screens: 2 of 23

  ⚠️  customer-search
      Changed (1):
        - src/pages/Customer/SearchPage.vue
      
  ⚠️  customer-edit
      Changed (2):
        - src/main/java/.../CustomerController.java
        - migration_001_customer.sql

✗ 2 screen(s) out of sync
```

### Drift Detection in CI/CD

Integrate drift detection into GitHub Actions:

```yaml
# .github/workflows/check-drift.yml
name: Check Documentation Drift

on:
  pull_request:
    paths:
      - 'src/**'
      - 'migrations/**'

jobs:
  drift:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - run: pip install -e .
      
      - name: Check for drift
        run: discover verify-drift . --spec-dir ./docs/screens
        
      - name: Report drift
        if: failure()
        run: |
          echo "::error::Documentation is out of sync with source code"
          echo "Run 'discover detect-screens . --include-backend' to regenerate"
```

## Step 3: Generate Screen Specs (Future Phase)

Once drift is detected or screens are new, regenerate specs:

```bash
# Generate markdown specs for all screens
discover generate-screen-specs ./data/screen_map.yaml --output ./docs/screens
```

(This command is planned for Phase 3 of screen-centric development.)

## Supported Menu Formats

### JSON Menu

```json
{
  "items": [
    {
      "id": "customers",
      "label": "Customers",
      "path": "/customers",
      "children": [
        {
          "id": "customer-search",
          "label": "Search",
          "path": "/customers/search"
        }
      ]
    }
  ]
}
```

### YAML Menu

```yaml
items:
  - id: customers
    label: Customers
    path: /customers
    children:
      - id: customer-search
        label: Search
        path: /customers/search
```

### TypeScript Constants

```typescript
// src/config/menu.ts
export const MENU = [
  {
    id: 'customers',
    label: 'Customers',
    path: '/customers',
    children: [
      {
        id: 'customer-search',
        label: 'Search',
        path: '/customers/search'
      }
    ]
  }
];
```

### Framework Routing

Vue Router (`src/router/index.ts`):
```typescript
const routes = [
  {
    path: '/customers',
    component: CustomerLayout,
    children: [
      {
        path: 'search',
        component: CustomerSearch
      }
    ]
  }
];
```

## Supported Backend Frameworks

### Java / Spring Boot

Detects:
- `@RestController`, `@Controller` annotations
- `@RequestMapping`, `@GetMapping`, `@PostMapping` endpoints
- `@Service`, `@Component`, `@Autowired` service injection
- `@Entity`, `@Table` database entities
- `RestTemplate`, `WebClient`, `@FeignClient` external calls

### .NET / ASP.NET Core

Detects:
- `[ApiController]`, `[Controller]` attributes
- `[HttpGet]`, `[HttpPost]` route attributes
- Service injection via constructors
- `[Table]`, `DbSet<T>` entity definitions
- `HttpClient`, `RestClient` external calls

### Node.js / Express

Detects:
- `router.get()`, `app.post()` route handlers
- `require()`, `import` module dependencies
- `mongoose.model()` schema definitions
- `axios`, `fetch`, `http` API calls

## Workflow Example: E-Commerce App

```bash
# 1. Detect screens from menu
$ discover detect-screens ~/projects/ecommerce --include-backend
[green]✓ Detected 47 screens[/]
  Wrote: data/screens/screen_map.yaml
  Wrote: data/screens/menu_tree.json
  Backend mapping: 42/47 screens mapped

# 2. Check if specs are still valid
$ discover verify-drift ~/projects/ecommerce --spec-dir ./docs/screens
✓ All specs are in sync with source code

# 3. (In future) Generate/regenerate specs
$ discover generate-screen-specs data/screens/screen_map.yaml --output docs/screens
Generated 47 screen specs in 12.3s

# 4. Review docs/screens/*.md in browser or editor
# Each spec includes:
#   - Purpose & user actions
#   - Backend APIs & services
#   - Database tables
#   - Batch jobs triggered
#   - Permissions
#   - Links to technical docs
```

## Output Structure

```
docs/
├── screens/                               # User-facing entry point
│   ├── customer-search.md                 # One spec per screen
│   ├── customer-edit.md
│   ├── order-detail.md
│   └── ...
├── screen-index.md                        # Navigation: menu tree → specs
├── database/                              # Supporting docs (generated separately)
│   ├── CUSTOMER.md
│   └── ORDER.md
├── backend-specs/                         # Supporting docs
│   ├── CustomerController.md
│   └── OrderService.md
└── batch-jobs/                            # Supporting docs
    └── OrderExportJob.md
```

Screen specs **link out** to supporting docs. No duplication—each piece of info lives in one canonical location.

## Troubleshooting

### No screens detected

**Problem**: Command runs but finds 0 screens

**Solution**:
1. Verify menu file exists: `ls menu.json` or `ls src/config/menu.yaml`
2. Check format matches one of the supported patterns
3. Ensure file is valid JSON/YAML: `jq . menu.json` or `python -c "import yaml; yaml.safe_load(open('menu.yaml'))"`

### Backend mapping fails for some screens

**Problem**: `Backend mapping: 35/47 screens mapped`

**Solution**:
1. Screens without FE components or with unclear API paths won't map
2. Run with `--no-backend` and manually add missing mappings
3. Create/clarify API call patterns in FE components

### Drift detected but no changes made

**Problem**: `discover verify-drift` reports drift but developers didn't change files

**Possible causes**:
1. File line endings changed (CRLF ↔ LF)
2. Whitespace changes in source files
3. File permissions changed
4. Build artifacts regenerated

**Solution**:
```bash
# Regenerate hashes with current source
discover detect-screens . --include-backend --output ./data
discover generate-screen-specs data/screen_map.yaml --output ./docs/screens
```

## Next Steps

1. **Generate specs**: Regenerate screen specs after detecting changes
2. **Customize**: Edit screen specs to add business context, validation rules
3. **Link**: Ensure specs link to supporting domain/backend/batch docs
4. **Monitor**: Add drift detection to CI/CD pipeline
5. **Export**: Export specs to confluence, wiki, or PDF

## See Also

- [`ENTITY_SERVICE_RESOLVER.md`](ENTITY_SERVICE_RESOLVER.md) — How entity-service linking works
- [`PERFORMANCE_PROFILING.md`](PERFORMANCE_PROFILING.md) — Performance tuning
- [Screen-Centric Overview in README](../../README.md#screen-centric-documentation-v03) — Conceptual overview
