/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { deserializeDateTime, serializeDateTime } from "@web/core/l10n/dates";

const { DateTime } = luxon;

const MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const TRIP_TYPE_LABELS = {
    delivery: "Customer Delivery",
    pickup: "Supplier Pickup",
    property: "Building Visit",
    internal: "Company Operation",
};

const TRIP_STATE_LABELS = {
    planned: "Planned",
    in_transit: "On the Road",
    completed: "Completed",
    cancelled: "Cancelled",
};

const SERVICE_STATE_LABELS = {
    new: "Scheduled",
    running: "In Progress",
    done: "Completed",
    cancelled: "Cancelled",
};

const TRIP_TYPE_PILLS = [
    { key: "delivery", label: "Customer Delivery" },
    { key: "pickup", label: "Supplier Pickup" },
    { key: "property", label: "Site Visit" },
    { key: "internal", label: "Internal" },
];

const UNIT_STATE_LABELS = {
    occupied: "Occupied",
    vacant: "Available",
    maintenance: "Maintenance",
};

const CATEGORY_LABELS = {
    all: "All Hardware",
    cement_aggregates: "Cement & Aggregates",
    paint_finishing: "Paint, Gypsum & Finishing",
    steel_iron: "Steel, Iron & Nails",
    timber_roofing: "Timber & Roofing",
    plumbing: "Plumbing & Pipes",
    electrical: "Electrical",
    tools_hardware: "Tools & General",
};

// Stock adjustment reasons, shown in Odoo's stock move history as the move name.
const ADJUST_REASONS = {
    add: [
        { key: "delivery", label: "Supplier delivery" },
        { key: "return", label: "Customer return" },
        { key: "found", label: "Found / correction" },
        { key: "opening", label: "Opening stock" },
    ],
    remove: [
        { key: "damaged", label: "Damaged" },
        { key: "lost", label: "Lost or expired" },
        { key: "theft", label: "Theft" },
        { key: "internal", label: "Used internally" },
        { key: "sample", label: "Sample / giveaway" },
    ],
    count: [
        { key: "count", label: "Physical count" },
        { key: "correction", label: "Data correction" },
    ],
};

// Space types that live inside the commercial building (residential houses are excluded).
const BUILDING_UNIT_DOMAIN = [["property_type", "!=", "house"]];

const UNASSIGNED_FLOOR = "Unassigned Floor";

// Client-side stale-while-revalidate cache so revisiting Command Center,
// Quick Sale, Stock Manager, or Vehicle Manager renders in 0ms while
// fresh data refreshes silently in the background.
let _ISANGE_RAW_CACHE = null;

function m2oName(value) {
    return Array.isArray(value) ? value[1] : "";
}

function m2oId(value) {
    return Array.isArray(value) ? value[0] : false;
}

/**
 * Convert a UTC datetime string coming from the server ("YYYY-MM-DD HH:MM:SS")
 * into a luxon DateTime in the user's timezone.
 */
function toLocal(value) {
    if (!value) {
        return null;
    }
    try {
        return deserializeDateTime(value);
    } catch {
        return null;
    }
}

function localMonthKey(value) {
    const dt = toLocal(value);
    return dt ? dt.toFormat("yyyy-MM") : "";
}

/**
 * Ranking used to stack floors from top (highest) to bottom (lowest).
 * Unrecognised floor names are grouped at the bottom, just above "Unassigned".
 */
function floorRank(name) {
    if (!name || name === UNASSIGNED_FLOOR) {
        return -1000;
    }
    const n = name.toLowerCase();
    const num = n.match(/(\d+)/);
    if (n.includes("basement") || n.includes("underground") || n.includes("sous-sol")) {
        return num ? -parseInt(num[1], 10) : -1;
    }
    if (n.includes("roof") || n.includes("terrace") || n.includes("penthouse")) {
        return 999;
    }
    if (n.includes("mezzanine")) {
        return 0.5;
    }
    if (n.includes("ground") || n === "gf" || n.includes("rez")) {
        return 0;
    }
    if (num) {
        return parseInt(num[1], 10);
    }
    return -500;
}

export class IsangeDashboard extends Component {
    static template = "isange_property_fleet.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");
        const autoOpenFastSale = Boolean(
            this.props.action &&
                ((this.props.action.context && this.props.action.context.open_fast_sale) ||
                    this.props.action.tag === "isange_fast_sale")
        );
        const autoOpenStock = Boolean(
            this.props.action &&
                ((this.props.action.context && this.props.action.context.open_stock_manager) ||
                    this.props.action.tag === "isange_stock_manager")
        );
        const autoOpenFleet = Boolean(
            this.props.action &&
                ((this.props.action.context && this.props.action.context.open_fleet_manager) ||
                    this.props.action.tag === "isange_fleet_manager")
        );
        this.state = useState({
            loading: true,
            greeting: "",
            today: "",
            currency: "RWF",
            kpi: {
                salesMonth: 0,
                salesCount: 0,
                purchasesMonth: 0,
                rentRoll: 0,
                potentialRent: 0,
                occupancyPct: 0,
                occupiedUnits: 0,
                totalUnits: 0,
                outstandingRent: 0,
                overdueCount: 0,
                fleetNet: 0,
                tripsMonth: 0,
                distanceMonth: 0,
                fuelMonth: 0,
                maintMonth: 0,
                feesMonth: 0,
                buildingProfit: 0,
                occupiedArea: 0,
                totalArea: 0,
            },
            floors: [],
            trend: [],
            trendMax: 1,
            mix: { hardware: 0, rent: 0, fleet: 0, total: 0 },
            upcomingRent: [],
            openInvoices: [],
            recentTrips: [],
            vehicles: [],
            // ---------------- Fast 20-Second Hardware Sale State ----------------
            fastSale: {
                open: autoOpenFastSale,
                busy: false,
                search: "",
                category: "all",
                categories: [],
                products: [],
                customers: [],
                partnerId: false,
                showNewCustomer: false,
                newCustomerName: "",
                newCustomerPhone: "",
                showNewItem: false,
                newItemName: "",
                newItemPrice: "",
                cart: [],
                applyVat: false,
                vatTaxId: false,
                vatRate: 18,
                autoPrint: false,
                addDelivery: false,
                vehicleId: false,
                destination: "",
                deliveryFee: 0,
                journals: {
                    cash: false,
                    momo: false,
                    bank: false,
                },
                splitOpen: false,
                split: {
                    cash: 0,
                    momo: 0,
                    bank: 0,
                },
                lastSale: null,
            },
            // ---------------- Stock & Inventory Manager State ----------------
            stockMgr: {
                open: autoOpenStock,
                busy: false,
                locationId: 5,
                search: "",
                filter: "all", // 'all' | 'low' | 'out'
                logAsPurchase: false,
                selectedId: false,
                tab: "adjust", // 'adjust' | 'pricing' | 'history'
                mode: "add", // 'add' | 'remove' | 'count'
                qty: "",
                reason: "delivery",
                note: "",
                usePO: false,
                history: [],
                historyLoading: false,
                supplierId: false,
                suppliers: [],
                showNewProduct: false,
                newName: "",
                newCategory: "cement_aggregates",
                newCost: "",
                newPrice: "",
                newQty: "",
                costValuation: 0,
                retailValuation: 0,
                projectedMargin: 0,
                totalUnitsOnHand: 0,
                healthyCount: 0,
                lowCount: 0,
                outCount: 0,
            },
            // ---------------- Vehicle & Fleet Management State ----------------
            fleetMgr: {
                open: autoOpenFleet,
                busy: false,
                period: "all", // 'month' | '6m' | 'all'
                monthKeyNow: "",
                sixStartKey: "",
                search: "",
                selectedId: false,
                tab: "travels", // 'travels' | 'maintenance' | 'pnl'
                vehicles: [],
                trips: [],
                services: [],
                serviceTypes: [],
                drivers: [],
                // Inline Log Travel form
                showNewTrip: false,
                tripType: "delivery",
                tripOrigin: "Isange Hardware Store",
                tripDestination: "",
                tripCargo: "",
                tripDriverId: false,
                tripPartnerId: false,
                tripDistance: "",
                tripOdoStart: "",
                tripOdoEnd: "",
                tripFuelLiters: "",
                tripFuelCost: "",
                tripOtherCost: "",
                tripFee: "",
                tripState: "completed",
                tripNotes: "",
                // Inline Log Maintenance form
                showNewService: false,
                svcTypeId: false,
                svcDate: "",
                svcAmount: "",
                svcOdometer: "",
                svcVendorId: false,
                svcDescription: "",
                svcNotes: "",
                svcState: "done",
                // Quick odometer update on Summary tab
                editOdo: "",
            },
        });
        onWillStart(async () => {
            if (_ISANGE_RAW_CACHE) {
                this._hydrateFromRaw(_ISANGE_RAW_CACHE);
                // Refresh fresh numbers silently without blocking render
                this.loadData({ background: true });
                return;
            }
            await this.loadData();
        });
    }

    // ------------------------------------------------------------------
    // Data loading (Single-RPC Bootstrap + 0ms SWR Client Cache)
    // ------------------------------------------------------------------

    async safe(promise, fallback) {
        try {
            return await promise;
        } catch (error) {
            console.warn("Isange Command Center: could not load a widget", error);
            return fallback;
        }
    }

    async loadCurrencyInfo() {
        try {
            const info = await this.orm.call("isange.property", "get_dashboard_currency_info", []);
            if (info && info.name) {
                return info;
            }
        } catch {
            // Fallback via standard searchRead when the Python worker has not restarted yet
        }
        const [companies, currencies] = await Promise.all([
            this.safe(this.orm.searchRead("res.company", [], ["currency_id"], { limit: 1 }), []),
            this.safe(this.orm.searchRead("res.currency", [["active", "=", true]], ["name", "symbol", "rate"]), []),
        ]);
        const compCurrId = companies.length ? m2oId(companies[0].currency_id) : false;
        const compCurrName = companies.length ? m2oName(companies[0].currency_id) : "RWF";
        const rates = {};
        let symbol = compCurrName || "RWF";
        for (const c of currencies) {
            rates[c.id] = c.id === compCurrId ? 1 : c.rate ? 1 / c.rate : 1;
            if (c.id === compCurrId && c.symbol) {
                symbol = c.symbol;
            }
        }
        return {
            name: compCurrName || "RWF",
            symbol,
            rates,
        };
    }

    async _fetchRawData(sixStartUtc) {
        // Fast path: 1 single server-side RPC instead of 17 parallel round-trips
        try {
            const bundled = await this.orm.call("isange.property", "get_command_center_data", [sixStartUtc]);
            if (bundled && bundled.currencyInfo) {
                if (!Array.isArray(bundled.products) || !bundled.products.length) {
                    const retryProds = await this.safe(
                        this.orm.searchRead(
                            "product.product",
                            [["sale_ok", "=", true], ["type", "!=", "service"]],
                            ["id", "name", "default_code", "list_price", "standard_price", "qty_available", "free_qty", "incoming_qty", "outgoing_qty", "is_storable", "uom_id", "construction_category", "taxes_id", "write_date"],
                            { order: "name asc", limit: 80 }
                        ),
                        []
                    );
                    if (retryProds.length) {
                        bundled.products = retryProds;
                    }
                }
                return bundled;
            }
        } catch (err) {
            console.warn("Isange Command Center: falling back to multi-RPC load", err);
        }

        const [
            currencyInfo,
            units,
            leases,
            rentInvoices,
            sales,
            posOrders,
            purchases,
            trips,
            vehicles,
            fleetServices,
            fleetServiceTypes,
            products,
            partners,
            journals,
            taxes,
            locations,
        ] = await Promise.all([
            this.loadCurrencyInfo(),
            this.safe(
                this.orm.searchRead(
                    "isange.property",
                    BUILDING_UNIT_DOMAIN,
                    ["name", "code", "neighborhood", "state", "monthly_rent", "current_tenant_id", "net_profit", "area_sqm", "property_type", "currency_id"],
                    { order: "code asc" }
                ),
                []
            ),
            this.safe(
                this.orm.searchRead(
                    "isange.lease",
                    [["state", "=", "active"]],
                    ["name", "property_id", "tenant_id", "rent_amount", "next_invoice_date", "billing_cycle", "currency_id"],
                    { order: "next_invoice_date asc" }
                ),
                []
            ),
            this.safe(
                this.orm.searchRead(
                    "account.move",
                    [
                        ["isange_property_id", "!=", false],
                        ["move_type", "=", "out_invoice"],
                        ["state", "=", "posted"],
                    ],
                    ["name", "partner_id", "amount_total_signed", "amount_residual_signed", "invoice_date", "invoice_date_due", "payment_state", "isange_property_id"],
                    { order: "invoice_date desc" }
                ),
                []
            ),
            this.safe(
                this.orm.searchRead(
                    "sale.order",
                    [
                        ["state", "in", ["sale", "done"]],
                        ["date_order", ">=", sixStartUtc],
                    ],
                    ["amount_total", "date_order", "currency_id"]
                ),
                []
            ),
            this.safe(
                this.orm.searchRead(
                    "pos.order",
                    [
                        ["state", "in", ["paid", "done", "invoiced"]],
                        ["date_order", ">=", sixStartUtc],
                    ],
                    ["amount_total", "date_order", "currency_id"]
                ),
                []
            ),
            this.safe(
                this.orm.searchRead(
                    "purchase.order",
                    [
                        ["state", "in", ["purchase", "done"]],
                        ["date_order", ">=", sixStartUtc],
                    ],
                    ["amount_total", "date_order", "currency_id"]
                ),
                []
            ),
            this.safe(
                this.orm.searchRead(
                    "isange.vehicle.trip",
                    [],
                    [
                        "name",
                        "date",
                        "date_arrival",
                        "vehicle_id",
                        "driver_id",
                        "partner_id",
                        "sale_order_id",
                        "origin_location",
                        "destination_location",
                        "cargo_description",
                        "trip_type",
                        "state",
                        "odometer_start",
                        "odometer_end",
                        "distance_km",
                        "fuel_liters",
                        "fuel_cost",
                        "other_cost",
                        "total_cost",
                        "delivery_fee",
                        "notes",
                        "currency_id",
                    ],
                    { order: "date desc, id desc", limit: 500 }
                ),
                []
            ),
            this.safe(
                this.orm.searchRead(
                    "fleet.vehicle",
                    [],
                    ["name", "license_plate", "driver_id", "odometer", "odometer_unit", "model_id", "state_id", "fuel_type"],
                    { order: "id asc", limit: 50 }
                ),
                []
            ),
            this.safe(
                this.orm.searchRead(
                    "fleet.vehicle.log.services",
                    [],
                    ["vehicle_id", "service_type_id", "date", "amount", "currency_id", "odometer", "vendor_id", "purchaser_id", "description", "notes", "state", "inv_ref"],
                    { order: "date desc, id desc", limit: 500 }
                ),
                []
            ),
            this.safe(
                this.orm.searchRead(
                    "fleet.service.type",
                    [["category", "=", "service"]],
                    ["id", "name", "category"],
                    { order: "id asc", limit: 30 }
                ),
                []
            ),
            this.safe(
                this.orm.searchRead(
                    "product.product",
                    [
                        ["sale_ok", "=", true],
                        ["type", "!=", "service"],
                    ],
                    ["id", "name", "default_code", "list_price", "standard_price", "qty_available", "free_qty", "incoming_qty", "outgoing_qty", "is_storable", "uom_id", "construction_category", "taxes_id", "write_date"],
                    { order: "name asc", limit: 80 }
                ),
                []
            ),
            this.safe(
                this.orm.searchRead("res.partner", [["active", "=", true], ["name", "not ilike", "(Driver)"]], ["id", "name", "phone", "city", "supplier_rank", "customer_rank"], {
                    order: "customer_rank asc, id desc",
                    limit: 100,
                }),
                []
            ),
            this.safe(
                this.orm.searchRead("account.journal", [["type", "in", ["cash", "bank"]]], ["id", "name", "type"]),
                []
            ),
            this.safe(
                this.orm.searchRead(
                    "account.tax",
                    [
                        ["type_tax_use", "=", "sale"],
                        ["active", "=", true],
                        ["amount", ">", 0],
                    ],
                    ["id", "name", "amount"],
                    { limit: 5 }
                ),
                []
            ),
            this.safe(
                this.orm.searchRead("stock.location", [["usage", "=", "internal"]], ["id", "name", "complete_name"], {
                    order: "id asc",
                    limit: 5,
                }),
                []
            ),
        ]);
        return {
            currencyInfo,
            units,
            leases,
            rentInvoices,
            sales,
            posOrders,
            purchases,
            trips,
            vehicles,
            fleetServices,
            fleetServiceTypes,
            products,
            partners,
            journals,
            taxes,
            locations,
        };
    }

    async loadData({ background = false } = {}) {
        const now = DateTime.local();
        const sixStart = now.startOf("month").minus({ months: 5 });
        const sixStartUtc = serializeDateTime(sixStart);
        try {
            const raw = await this._fetchRawData(sixStartUtc);
            _ISANGE_RAW_CACHE = raw;
            this._hydrateFromRaw(raw);
        } catch (err) {
            if (!background) {
                console.warn("Isange Command Center load error", err);
                this.state.loading = false;
            }
        }
    }

    _hydrateFromRaw(raw) {
        const now = DateTime.local();
        const todayStr = now.toISODate();
        const monthKeyNow = now.toFormat("yyyy-MM");
        const sixStart = now.startOf("month").minus({ months: 5 });
        const in30Str = now.plus({ days: 30 }).toISODate();

        const hour = now.hour;
        this.state.greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
        this.state.today = now.setLocale("en-GB").toFormat("cccc d LLLL yyyy");

        const currencyInfo = raw.currencyInfo || { name: "RWF", symbol: "RWF", rates: {} };
        const units = raw.units || [];
        const leases = raw.leases || [];
        const rentInvoices = raw.rentInvoices || [];
        const sales = raw.sales || [];
        const posOrders = raw.posOrders || [];
        const purchases = raw.purchases || [];
        const trips = raw.trips || [];
        const vehicles = raw.vehicles || [];
        const fleetServices = raw.fleetServices || [];
        const fleetServiceTypes = raw.fleetServiceTypes || [];
        const rawProducts = raw.products || [];
        const rawPartners = raw.partners || [];
        const rawJournals = raw.journals || [];
        const rawTaxes = raw.taxes || [];
        const rawLocations = raw.locations || [];

        // ---------------- Currency conversion (to company currency) ----------------
        const rates = currencyInfo.rates || {};
        const conv = (amount, currencyField) => {
            const id = m2oId(currencyField);
            const rate = id && rates[id] !== undefined ? rates[id] : 1;
            return (amount || 0) * rate;
        };
        this.state.currency = currencyInfo.name || currencyInfo.symbol || "RWF";

        // ---------------- Months axis (last 6 months, user timezone) ----------------
        const months = [];
        for (let i = 5; i >= 0; i--) {
            const d = now.startOf("month").minus({ months: i });
            months.push({
                key: d.toFormat("yyyy-MM"),
                label: MONTH_LABELS[d.month - 1],
                hardware: 0,
                rent: 0,
                fleet: 0,
            });
        }
        const monthIndex = {};
        months.forEach((m, idx) => {
            monthIndex[m.key] = idx;
        });
        const addToMonth = (monthKey, field, amount) => {
            const idx = monthIndex[monthKey];
            if (idx !== undefined) {
                months[idx][field] += amount || 0;
            }
        };

        // ---------------- Hardware store ----------------
        let salesMonth = 0;
        let salesCount = 0;
        for (const order of [...sales, ...posOrders]) {
            const key = localMonthKey(order.date_order);
            const amount = conv(order.amount_total, order.currency_id);
            addToMonth(key, "hardware", amount);
            if (key === monthKeyNow) {
                salesMonth += amount;
                salesCount += 1;
            }
        }
        let purchasesMonth = 0;
        for (const po of purchases) {
            if (localMonthKey(po.date_order) === monthKeyNow) {
                purchasesMonth += conv(po.amount_total, po.currency_id);
            }
        }

        // ---------------- Commercial building ----------------
        let occupiedUnits = 0;
        let rentRoll = 0;
        let potentialRent = 0;
        let buildingProfit = 0;
        let occupiedArea = 0;
        let totalArea = 0;
        const floorsMap = {};
        for (const unit of units) {
            const rent = conv(unit.monthly_rent, unit.currency_id);
            potentialRent += rent;
            buildingProfit += conv(unit.net_profit, unit.currency_id);
            totalArea += unit.area_sqm || 0;
            if (unit.state === "occupied") {
                occupiedUnits += 1;
                rentRoll += rent;
                occupiedArea += unit.area_sqm || 0;
            }
            const floorName = (unit.neighborhood || "").trim() || UNASSIGNED_FLOOR;
            if (!floorsMap[floorName]) {
                floorsMap[floorName] = { name: floorName, rank: floorRank(floorName), units: [], occupied: 0 };
            }
            floorsMap[floorName].units.push({
                id: unit.id,
                code: unit.code || "",
                name: unit.name,
                state: unit.state,
                stateLabel: UNIT_STATE_LABELS[unit.state] || unit.state,
                rent,
                area: unit.area_sqm || 0,
                tenant: m2oName(unit.current_tenant_id),
            });
            if (unit.state === "occupied") {
                floorsMap[floorName].occupied += 1;
            }
        }
        const floors = Object.values(floorsMap).sort((a, b) => b.rank - a.rank || a.name.localeCompare(b.name));

        let outstandingRent = 0;
        let overdueCount = 0;
        const openInvoices = [];
        for (const inv of rentInvoices) {
            const total = Math.abs(inv.amount_total_signed || 0);
            const residual = Math.abs(inv.amount_residual_signed || 0);
            addToMonth(String(inv.invoice_date || "").slice(0, 7), "rent", total);
            const isOpen = residual > 0 && !["paid", "in_payment", "reversed"].includes(inv.payment_state);
            if (isOpen) {
                outstandingRent += residual;
                const overdue = !!inv.invoice_date_due && inv.invoice_date_due < todayStr;
                if (overdue) {
                    overdueCount += 1;
                }
                openInvoices.push({
                    id: inv.id,
                    name: inv.name,
                    tenant: m2oName(inv.partner_id),
                    initials: this.initials(m2oName(inv.partner_id)),
                    unit: m2oName(inv.isange_property_id),
                    due: inv.invoice_date_due || "",
                    total,
                    residual,
                    overdue,
                });
            }
        }

        const upcomingRent = [];
        for (const lease of leases) {
            if (lease.next_invoice_date && lease.next_invoice_date <= in30Str) {
                upcomingRent.push({
                    id: lease.id,
                    name: lease.name,
                    tenant: m2oName(lease.tenant_id),
                    initials: this.initials(m2oName(lease.tenant_id)),
                    unit: m2oName(lease.property_id),
                    due: lease.next_invoice_date,
                    amount: conv(lease.rent_amount, lease.currency_id),
                    overdue: lease.next_invoice_date < todayStr,
                    dueToday: lease.next_invoice_date === todayStr,
                });
            }
        }

        // ---------------- Fleet ----------------
        let tripsMonth = 0;
        let distanceMonth = 0;
        let fuelMonth = 0;
        let feesMonth = 0;
        let costMonth = 0;
        let maintMonth = 0;

        const normalizedTrips = trips.map((trip) => {
            const localDate = toLocal(trip.date);
            const monthKey = localDate ? localDate.toFormat("yyyy-MM") : "";
            const fee = conv(trip.delivery_fee, trip.currency_id);
            const fuelCost = conv(trip.fuel_cost, trip.currency_id);
            const otherCost = conv(trip.other_cost, trip.currency_id);
            const totalCost = conv(trip.total_cost, trip.currency_id) || fuelCost + otherCost;
            if (trip.state !== "cancelled") {
                addToMonth(monthKey, "fleet", fee);
                if (monthKey === monthKeyNow) {
                    tripsMonth += 1;
                    distanceMonth += trip.distance_km || 0;
                    fuelMonth += fuelCost;
                    feesMonth += fee;
                    costMonth += totalCost;
                }
            }
            return {
                id: trip.id,
                name: trip.name,
                vehicleId: m2oId(trip.vehicle_id),
                vehicle: m2oName(trip.vehicle_id),
                driverId: m2oId(trip.driver_id),
                driver: m2oName(trip.driver_id) || "Unassigned",
                partner: m2oName(trip.partner_id),
                saleOrder: m2oName(trip.sale_order_id),
                date: localDate ? localDate.toFormat("yyyy-MM-dd HH:mm") : "",
                shortDate: localDate ? localDate.toFormat("dd LLL, HH:mm") : "",
                monthKey,
                origin: trip.origin_location || "Isange Hardware Store",
                destination: trip.destination_location || "",
                cargo: trip.cargo_description || "",
                tripType: trip.trip_type || "delivery",
                type: TRIP_TYPE_LABELS[trip.trip_type] || trip.trip_type,
                state: trip.state,
                stateLabel: TRIP_STATE_LABELS[trip.state] || trip.state,
                odometerStart: Number(trip.odometer_start) || 0,
                odometerEnd: Number(trip.odometer_end) || 0,
                distance: Number(trip.distance_km) || 0,
                fuelLiters: Number(trip.fuel_liters) || 0,
                fuelCost,
                otherCost,
                cost: totalCost,
                fee,
                net: fee - totalCost,
                notes: trip.notes || "",
            };
        });

        const normalizedServices = fleetServices.map((svc) => {
            const rawDate = svc.date || "";
            const monthKey = String(rawDate).slice(0, 7);
            const amount = conv(svc.amount, svc.currency_id);
            if (svc.state !== "cancelled" && monthKey === monthKeyNow) {
                maintMonth += amount;
            }
            return {
                id: svc.id,
                vehicleId: m2oId(svc.vehicle_id),
                vehicle: m2oName(svc.vehicle_id),
                serviceTypeId: m2oId(svc.service_type_id),
                serviceType: m2oName(svc.service_type_id) || "Maintenance",
                date: rawDate,
                monthKey,
                amount,
                odometer: Number(svc.odometer) || 0,
                vendor: m2oName(svc.vendor_id),
                driver: m2oName(svc.purchaser_id),
                description: svc.description || "",
                notes: svc.notes || "",
                invRef: svc.inv_ref || "",
                state: svc.state || "done",
                stateLabel: SERVICE_STATE_LABELS[svc.state] || svc.state || "Done",
            };
        });

        const recentTrips = normalizedTrips.slice(0, 6);

        // Build per-vehicle lifetime summary for overview Fleet Roster cards
        const vehLifetime = {};
        for (const t of normalizedTrips) {
            if (t.state === "cancelled" || !t.vehicleId) {
                continue;
            }
            const agg = (vehLifetime[t.vehicleId] ||= { income: 0, tripCost: 0, maint: 0, trips: 0, km: 0, active: 0 });
            agg.income += t.fee;
            agg.tripCost += t.cost;
            agg.trips += 1;
            agg.km += t.distance;
            if (t.state === "in_transit") {
                agg.active += 1;
            }
        }
        for (const s of normalizedServices) {
            if (s.state === "cancelled" || !s.vehicleId) {
                continue;
            }
            const agg = (vehLifetime[s.vehicleId] ||= { income: 0, tripCost: 0, maint: 0, trips: 0, km: 0, active: 0 });
            agg.maint += s.amount;
        }

        const vehicleCards = vehicles.map((v) => {
            const agg = vehLifetime[v.id] || { income: 0, tripCost: 0, maint: 0, trips: 0, km: 0, active: 0 };
            return {
                id: v.id,
                name: m2oName(v.model_id) || v.name,
                fullName: v.name,
                plate: (v.license_plate || "—").toUpperCase(),
                driver: m2oName(v.driver_id) || "No driver assigned",
                odometer: Number(v.odometer) || 0,
                odometerUnit: v.odometer_unit === "miles" ? "mi" : "km",
                driverId: m2oId(v.driver_id),
                stateName: m2oName(v.state_id) || "",
                fuelType: v.fuel_type || "",
                income: agg.income,
                maintenance: agg.maint,
                tripCost: agg.tripCost,
                net: agg.income - agg.tripCost - agg.maint,
                tripsCount: agg.trips,
                distanceKm: agg.km,
                activeTripsCount: agg.active,
            };
        });

        // ---------------- Trend + mix ----------------
        let trendMax = 1;
        let mixHardware = 0;
        let mixRent = 0;
        let mixFleet = 0;
        for (const m of months) {
            const total = m.hardware + m.rent + m.fleet;
            trendMax = Math.max(trendMax, total);
            mixHardware += m.hardware;
            mixRent += m.rent;
            mixFleet += m.fleet;
        }
        for (const m of months) {
            m.total = m.hardware + m.rent + m.fleet;
            m.hPct = (m.hardware / trendMax) * 100;
            m.rPct = (m.rent / trendMax) * 100;
            m.fPct = (m.fleet / trendMax) * 100;
        }

        // ---------------- Fast 20-Second Sale & Stock Catalog Setup ----------------
        const seenNames = new Set();
        const catalog = [];
        const presentCats = new Set(["all"]);
        let costValuation = 0;
        let retailValuation = 0;
        let totalUnitsOnHand = 0;
        let healthyCount = 0;
        let lowCount = 0;
        let outCount = 0;

        for (const p of rawProducts) {
            if (["TIPS", "EXP_GEN", "COMM", "FOOD", "GIFT", "MIL", "TRANS & ACC"].includes(p.default_code)) {
                continue;
            }
            if ((p.name || "").toLowerCase().includes("down payment")) {
                continue;
            }
            const normName = (p.name || "").trim().toUpperCase();
            if (seenNames.has(normName)) {
                continue;
            }
            seenNames.add(normName);
            const cat = p.construction_category || "tools_hardware";
            presentCats.add(cat);
            const unique = encodeURIComponent(p.write_date || "");
            const qty = Number(p.qty_available) || 0;
            const cost = Number(p.standard_price) || 0;
            const price = Number(p.list_price) || 0;
            const stockState = qty <= 0 ? "out" : qty <= 15 ? "low" : "ok";
            const stockLabel = qty <= 0 ? "Out of stock" : qty <= 15 ? `Low: ${qty}` : `${qty} in stock`;

            if (qty > 0) {
                costValuation += qty * cost;
                retailValuation += qty * price;
                totalUnitsOnHand += qty;
            }
            if (stockState === "out") {
                outCount += 1;
            } else if (stockState === "low") {
                lowCount += 1;
            } else {
                healthyCount += 1;
            }

            const marginAmt = price - cost;
            const marginPct = price > 0 ? Math.round(((price - cost) / price) * 100) : 0;

            catalog.push({
                id: p.id,
                name: p.name,
                price,
                cost,
                qty,
                incoming: Number(p.incoming_qty) || 0,
                outgoing: Number(p.outgoing_qty) || 0,
                stockState,
                stockLabel,
                marginAmt,
                marginPct,
                editQty: qty,
                receiveQty: 20,
                editCost: cost,
                editPrice: price,
                uom: m2oName(p.uom_id) || "Units",
                category: cat,
                categoryLabel: CATEGORY_LABELS[cat] || "Hardware",
                taxIds: Array.isArray(p.taxes_id) ? p.taxes_id : [],
                imageUrl: `/web/image/product.product/${p.id}/image_256?unique=${unique}`,
            });
        }
        // Sort priced items first, then alphabetically
        catalog.sort((a, b) => (b.price > 0) - (a.price > 0) || a.name.localeCompare(b.name));

        const categoryTabs = Array.from(presentCats).map((key) => ({
            key,
            label: CATEGORY_LABELS[key] || key,
        }));

        // Customers list: put walk-in / cashier first, then real customers
        const excludedPartnerNames = new Set([
            "Administrator",
            "Isange Hardware",
            "Isange Hardware Ltd",
            "Rentals & Fleet Manager",
            "Storekeeper (Inventory & Purchase)",
            "Eric Ndayisaba (Driver)",
            "GLOBAL BUSINESS EXOR",
        ]);
        const isDriverOrExcluded = (name) =>
            excludedPartnerNames.has(name) || String(name || "").includes("(Driver)");
        let walkInPartner = rawPartners.find((pt) => (pt.name || "").toLowerCase().includes("walk-in") || (pt.name || "").toLowerCase().includes("cashier"));
        const customerList = [];
        if (walkInPartner) {
            customerList.push({
                id: walkInPartner.id,
                name: "Walk-in Customer (Cash Sale)",
                shortName: "Walk-in Customer",
                phone: walkInPartner.phone || "",
                city: walkInPartner.city || "Kigali",
            });
        }
        for (const pt of rawPartners) {
            if (walkInPartner && pt.id === walkInPartner.id) {
                continue;
            }
            if (isDriverOrExcluded(pt.name)) {
                continue;
            }
            customerList.push({
                id: pt.id,
                name: pt.name,
                shortName: (pt.name || "").replace(/\s*\(.*?\)/g, "").trim(),
                phone: pt.phone || "",
                city: pt.city || "Kigali",
            });
        }
        if (!customerList.length && rawPartners.length) {
            customerList.push({
                id: rawPartners[0].id,
                name: rawPartners[0].name,
                shortName: rawPartners[0].name,
                phone: rawPartners[0].phone || "",
                city: "Kigali",
            });
        }

        // Journals: Cash, MoMo, Bank
        let cashJournalId = false;
        let momoJournalId = false;
        let bankJournalId = false;
        for (const j of rawJournals) {
            const jName = (j.name || "").toLowerCase();
            if (j.type === "cash" && !cashJournalId) {
                cashJournalId = j.id;
            } else if ((jName.includes("momo") || jName.includes("mobile")) && !momoJournalId) {
                momoJournalId = j.id;
            } else if (j.type === "bank" && !bankJournalId) {
                bankJournalId = j.id;
            }
        }
        if (!momoJournalId) {
            momoJournalId = bankJournalId || cashJournalId;
        }
        if (!bankJournalId) {
            bankJournalId = momoJournalId || cashJournalId;
        }
        if (!cashJournalId) {
            cashJournalId = momoJournalId || bankJournalId;
        }

        const vatTax = rawTaxes.find((t) => t.amount === 18) || rawTaxes[0] || null;

        Object.assign(this.state.kpi, {
            salesMonth,
            salesCount,
            purchasesMonth,
            rentRoll,
            potentialRent,
            occupancyPct: units.length ? Math.round((occupiedUnits / units.length) * 100) : 0,
            occupiedUnits,
            totalUnits: units.length,
            outstandingRent,
            overdueCount,
            fleetNet: feesMonth - costMonth - maintMonth,
            tripsMonth,
            distanceMonth,
            fuelMonth,
            maintMonth,
            feesMonth,
            buildingProfit,
            occupiedArea,
            totalArea,
        });
        this.state.floors = floors;
        this.state.trend = months;
        this.state.trendMax = trendMax;
        this.state.mix = {
            hardware: mixHardware,
            rent: mixRent,
            fleet: mixFleet,
            total: mixHardware + mixRent + mixFleet,
        };
        this.state.upcomingRent = upcomingRent.slice(0, 6);
        this.state.openInvoices = openInvoices.slice(0, 6);
        this.state.recentTrips = recentTrips;
        this.state.vehicles = vehicleCards;

        const fs = this.state.fastSale;
        fs.products = catalog;
        fs.categories = categoryTabs;
        fs.customers = customerList;
        if (!fs.partnerId && customerList.length) {
            fs.partnerId = customerList[0].id;
        }
        if (!fs.vehicleId && vehicleCards.length) {
            fs.vehicleId = vehicleCards[0].id;
        }
        fs.journals = {
            cash: cashJournalId,
            momo: momoJournalId,
            bank: bankJournalId,
        };
        if (vatTax) {
            fs.vatTaxId = vatTax.id;
            fs.vatRate = vatTax.amount || 18;
        }

        const sm = this.state.stockMgr;
        if (rawLocations.length) {
            const whStock = rawLocations.find((l) => (l.complete_name || "").includes("Stock")) || rawLocations[0];
            sm.locationId = whStock.id;
        }
        const supplierList = rawPartners
            .filter((pt) => !isDriverOrExcluded(pt.name) && !(pt.name || "").toLowerCase().includes("walk-in"))
            .map((pt) => ({
                id: pt.id,
                name: pt.name,
                shortName: (pt.name || "").replace(/\s*\(.*?\)/g, "").trim(),
            }));
        // Put suppliers / East Africa Building Supplies first as default supplier
        supplierList.sort((a, b) => b.name.toLowerCase().includes("supplier") - a.name.toLowerCase().includes("supplier") || b.name.toLowerCase().includes("supplies") - a.name.toLowerCase().includes("supplies"));
        sm.suppliers = supplierList;
        if (!sm.supplierId && supplierList.length) {
            sm.supplierId = supplierList[0].id;
        }
        sm.costValuation = costValuation;
        sm.retailValuation = retailValuation;
        sm.projectedMargin = Math.max(0, retailValuation - costValuation);
        sm.totalUnitsOnHand = totalUnitsOnHand;
        sm.healthyCount = healthyCount;
        sm.lowCount = lowCount;
        sm.outCount = outCount;

        // Hydrate Vehicle & Fleet Manager state
        const fm = this.state.fleetMgr;
        fm.monthKeyNow = monthKeyNow;
        fm.sixStartKey = sixStart.toFormat("yyyy-MM");
        fm.vehicles = vehicleCards;
        fm.trips = normalizedTrips;
        fm.services = normalizedServices;
        fm.serviceTypes = fleetServiceTypes.map((st) => ({ id: st.id, name: st.name }));
        if (!fm.svcTypeId && fm.serviceTypes.length) {
            fm.svcTypeId = fm.serviceTypes[0].id;
        }
        if (!fm.svcDate) {
            fm.svcDate = todayStr;
        }
        // Build driver list: vehicle drivers first, then partners
        const driverMap = new Map();
        for (const v of vehicleCards) {
            if (v.driverId && !driverMap.has(v.driverId)) {
                driverMap.set(v.driverId, { id: v.driverId, name: v.driver });
            }
        }
        for (const pt of rawPartners) {
            if (!driverMap.has(pt.id) && !["Administrator"].includes(pt.name)) {
                driverMap.set(pt.id, { id: pt.id, name: pt.name });
            }
        }
        const driverList = Array.from(driverMap.values());
        driverList.sort((a, b) => b.name.toLowerCase().includes("driver") - a.name.toLowerCase().includes("driver"));
        fm.drivers = driverList;
        if (fm.selectedId && !vehicleCards.some((v) => v.id === fm.selectedId)) {
            fm.selectedId = false;
        }
        if (!fm.selectedId && vehicleCards.length) {
            fm.selectedId = vehicleCards[0].id;
            fm.editOdo = String(vehicleCards[0].odometer || 0);
        } else if (fm.selectedId) {
            const curVeh = vehicleCards.find((v) => v.id === fm.selectedId);
            if (curVeh) {
                fm.editOdo = String(curVeh.odometer || 0);
            }
        }

        this.state.loading = false;
    }

    // ------------------------------------------------------------------
    // Vehicle & Fleet Management (Income, Maintenance & Travels)
    // ------------------------------------------------------------------

    toggleFleetManager(forceOpen = null, vehicleId = null) {
        const fm = this.state.fleetMgr;
        const nextOpen = forceOpen !== null ? forceOpen : !fm.open;
        fm.open = nextOpen;
        if (nextOpen) {
            this.state.fastSale.open = false;
            this.state.stockMgr.open = false;
            if (vehicleId) {
                const target = fm.vehicles.find((v) => v.id === vehicleId);
                if (target) {
                    this.selectFleetVehicle(target);
                }
            } else if (!fm.selectedId && fm.vehicles.length) {
                this.selectFleetVehicle(fm.vehicles[0]);
            }
            const scroller = document.querySelector(".o_isange_dashboard");
            if (scroller && scroller.scrollTop > 120) {
                scroller.scrollTo({ top: 0, behavior: "smooth" });
            }
        }
    }

    selectFleetVehicle(v) {
        const fm = this.state.fleetMgr;
        fm.selectedId = v.id;
        fm.showNewTrip = false;
        fm.showNewService = false;
        fm.editOdo = String(v.odometer || 0);
        fm.tripDriverId = v.driverId || (fm.drivers[0] ? fm.drivers[0].id : false);
        fm.tripOdoStart = String(v.odometer || 0);
        fm.svcOdometer = String(v.odometer || 0);
    }

    setFleetPeriod(period) {
        this.state.fleetMgr.period = period;
    }

    setFleetTab(tab) {
        const fm = this.state.fleetMgr;
        fm.tab = tab;
        fm.showNewTrip = false;
        fm.showNewService = false;
    }

    get fleetPeriodLabel() {
        const p = this.state.fleetMgr.period;
        return p === "month" ? "This Month" : p === "6m" ? "Last 6 Months" : "All Time";
    }

    get tripTypePills() {
        return TRIP_TYPE_PILLS;
    }

    _fleetInPeriod(monthKey) {
        const fm = this.state.fleetMgr;
        if (fm.period === "all") {
            return true;
        }
        if (!monthKey) {
            return false;
        }
        if (fm.period === "month") {
            return monthKey === fm.monthKeyNow;
        }
        return monthKey >= fm.sixStartKey;
    }

    get fleetVehicleStats() {
        const fm = this.state.fleetMgr;
        const map = new Map();
        for (const v of fm.vehicles) {
            map.set(v.id, {
                ...v,
                income: 0,
                maintenance: 0,
                fuelCost: 0,
                otherCost: 0,
                tripCost: 0,
                totalCost: 0,
                net: 0,
                tripsCount: 0,
                activeTripsCount: 0,
                distanceKm: 0,
                fuelLiters: 0,
                servicesCount: 0,
                costPerKm: 0,
                avgIncomePerTrip: 0,
                lastTripDate: "",
                lastServiceDate: "",
                incomeBarPct: 0,
                maintBarPct: 0,
                fuelBarPct: 0,
            });
        }

        for (const t of fm.trips) {
            if (t.state === "cancelled" || !this._fleetInPeriod(t.monthKey)) {
                continue;
            }
            const agg = map.get(t.vehicleId);
            if (!agg) {
                continue;
            }
            agg.income += t.fee;
            agg.fuelCost += t.fuelCost;
            agg.otherCost += t.otherCost;
            agg.tripCost += t.cost;
            agg.tripsCount += 1;
            agg.distanceKm += t.distance;
            agg.fuelLiters += t.fuelLiters;
            if (t.state === "in_transit") {
                agg.activeTripsCount += 1;
            }
            if (!agg.lastTripDate && t.shortDate) {
                agg.lastTripDate = t.shortDate;
            }
        }

        for (const s of fm.services) {
            if (s.state === "cancelled" || !this._fleetInPeriod(s.monthKey)) {
                continue;
            }
            const agg = map.get(s.vehicleId);
            if (!agg) {
                continue;
            }
            agg.maintenance += s.amount;
            agg.servicesCount += 1;
            if (!agg.lastServiceDate && s.date) {
                agg.lastServiceDate = s.date;
            }
        }

        const list = Array.from(map.values()).map((agg) => {
            agg.totalCost = agg.maintenance + agg.tripCost;
            agg.net = agg.income - agg.totalCost;
            agg.costPerKm = agg.distanceKm > 0 ? Math.round(agg.totalCost / agg.distanceKm) : 0;
            agg.avgIncomePerTrip = agg.tripsCount > 0 ? Math.round(agg.income / agg.tripsCount) : 0;
            const scale = Math.max(agg.income, agg.totalCost, 1);
            agg.incomeBarPct = Math.round((agg.income / scale) * 100);
            agg.maintBarPct = Math.round((agg.maintenance / scale) * 100);
            agg.fuelBarPct = Math.round((agg.tripCost / scale) * 100);
            return agg;
        });

        list.sort(
            (a, b) =>
                b.activeTripsCount - a.activeTripsCount ||
                b.income - a.income ||
                b.tripsCount - a.tripsCount ||
                a.plate.localeCompare(b.plate)
        );
        return list;
    }

    get filteredFleetVehicles() {
        const q = (this.state.fleetMgr.search || "").trim().toLowerCase();
        const stats = this.fleetVehicleStats;
        if (!q) {
            return stats;
        }
        return stats.filter(
            (v) =>
                (v.plate || "").toLowerCase().includes(q) ||
                (v.name || "").toLowerCase().includes(q) ||
                (v.driver || "").toLowerCase().includes(q)
        );
    }

    get fleetSummary() {
        let income = 0;
        let maintenance = 0;
        let fuelCost = 0;
        let otherCost = 0;
        let tripCost = 0;
        let tripsCount = 0;
        let activeTripsCount = 0;
        let distanceKm = 0;
        let fuelLiters = 0;
        let servicesCount = 0;

        for (const v of this.fleetVehicleStats) {
            income += v.income;
            maintenance += v.maintenance;
            fuelCost += v.fuelCost;
            otherCost += v.otherCost;
            tripCost += v.tripCost;
            tripsCount += v.tripsCount;
            activeTripsCount += v.activeTripsCount;
            distanceKm += v.distanceKm;
            fuelLiters += v.fuelLiters;
            servicesCount += v.servicesCount;
        }
        const totalExpenses = maintenance + tripCost;
        const net = income - totalExpenses;
        const marginPct = income > 0 ? Math.round((net / income) * 100) : 0;
        return {
            income,
            maintenance,
            fuelCost,
            otherCost,
            tripCost,
            totalExpenses,
            net,
            marginPct,
            tripsCount,
            activeTripsCount,
            distanceKm,
            fuelLiters,
            servicesCount,
        };
    }

    get selectedFleetVehicle() {
        const stats = this.fleetVehicleStats;
        if (!stats.length) {
            return null;
        }
        const id = this.state.fleetMgr.selectedId;
        return stats.find((v) => v.id === id) || stats[0];
    }

    get selectedVehicleTrips() {
        const veh = this.selectedFleetVehicle;
        if (!veh) {
            return [];
        }
        return this.state.fleetMgr.trips.filter(
            (t) => t.vehicleId === veh.id && this._fleetInPeriod(t.monthKey)
        );
    }

    get selectedVehicleServices() {
        const veh = this.selectedFleetVehicle;
        if (!veh) {
            return [];
        }
        return this.state.fleetMgr.services.filter(
            (s) => s.vehicleId === veh.id && this._fleetInPeriod(s.monthKey)
        );
    }

    get selectedVehicleMaintByType() {
        const svcs = this.selectedVehicleServices.filter((s) => s.state !== "cancelled");
        const groups = {};
        let total = 0;
        for (const s of svcs) {
            const key = s.serviceType || "Maintenance";
            if (!groups[key]) {
                groups[key] = { name: key, amount: 0, count: 0 };
            }
            groups[key].amount += s.amount;
            groups[key].count += 1;
            total += s.amount;
        }
        return Object.values(groups)
            .map((g) => ({ ...g, pct: total > 0 ? Math.round((g.amount / total) * 100) : 0 }))
            .sort((a, b) => b.amount - a.amount);
    }

    get newTripCalc() {
        const fm = this.state.fleetMgr;
        const fee = Math.max(0, parseFloat(fm.tripFee) || 0);
        const fuel = Math.max(0, parseFloat(fm.tripFuelCost) || 0);
        const other = Math.max(0, parseFloat(fm.tripOtherCost) || 0);
        const totalCost = fuel + other;
        const odoStart = Math.max(0, parseFloat(fm.tripOdoStart) || 0);
        const odoEnd = Math.max(0, parseFloat(fm.tripOdoEnd) || 0);
        const typedDist = Math.max(0, parseFloat(fm.tripDistance) || 0);
        const distance = odoEnd > odoStart ? odoEnd - odoStart : typedDist;
        return {
            fee,
            fuel,
            other,
            totalCost,
            net: fee - totalCost,
            distance,
        };
    }

    openNewTripForm() {
        const fm = this.state.fleetMgr;
        const veh = this.selectedFleetVehicle;
        fm.tab = "travels";
        fm.showNewService = false;
        fm.showNewTrip = !fm.showNewTrip;
        if (fm.showNewTrip && veh) {
            fm.tripType = "delivery";
            fm.tripOrigin = "Isange Hardware Store";
            fm.tripDestination = "";
            fm.tripCargo = "";
            fm.tripDriverId = veh.driverId || (fm.drivers[0] ? fm.drivers[0].id : false);
            fm.tripPartnerId = false;
            fm.tripOdoStart = String(veh.odometer || 0);
            fm.tripOdoEnd = "";
            fm.tripDistance = "";
            fm.tripFuelLiters = "";
            fm.tripFuelCost = "";
            fm.tripOtherCost = "";
            fm.tripFee = "";
            fm.tripState = "completed";
            fm.tripNotes = "";
        }
    }

    setTripType(type) {
        this.state.fleetMgr.tripType = type;
    }

    setTripState(st) {
        this.state.fleetMgr.tripState = st;
    }

    setTripDriverId(val) {
        this.state.fleetMgr.tripDriverId = Number(val) || false;
    }

    setTripPartnerId(val) {
        this.state.fleetMgr.tripPartnerId = Number(val) || false;
    }

    async saveQuickTrip() {
        const fm = this.state.fleetMgr;
        const veh = this.selectedFleetVehicle;
        if (!veh || fm.busy) {
            return;
        }
        const destination = (fm.tripDestination || "").trim();
        if (!destination) {
            this.notification.add("Enter the travel destination first.", { type: "warning" });
            return;
        }
        const driverId = fm.tripDriverId || veh.driverId || (fm.drivers[0] ? fm.drivers[0].id : false);
        if (!driverId) {
            this.notification.add("Select a driver for this trip.", { type: "warning" });
            return;
        }
        const odoStart = Math.max(0, parseFloat(fm.tripOdoStart) || veh.odometer || 0);
        const typedOdoEnd = Math.max(0, parseFloat(fm.tripOdoEnd) || 0);
        const typedDist = Math.max(0, parseFloat(fm.tripDistance) || 0);
        const odoEnd = typedOdoEnd > odoStart ? typedOdoEnd : typedDist > 0 ? odoStart + typedDist : 0;
        const fee = Math.max(0, parseFloat(fm.tripFee) || 0);
        const fuelCost = Math.max(0, parseFloat(fm.tripFuelCost) || 0);
        const otherCost = Math.max(0, parseFloat(fm.tripOtherCost) || 0);
        const fuelLiters = Math.max(0, parseFloat(fm.tripFuelLiters) || 0);

        fm.busy = true;
        try {
            const vals = {
                vehicle_id: veh.id,
                driver_id: Number(driverId),
                trip_type: fm.tripType || "delivery",
                origin_location: (fm.tripOrigin || "Isange Hardware Store").trim(),
                destination_location: destination,
                cargo_description: (fm.tripCargo || "").trim() || false,
                partner_id: fm.tripPartnerId ? Number(fm.tripPartnerId) : false,
                odometer_start: odoStart,
                odometer_end: odoEnd,
                fuel_liters: fuelLiters,
                fuel_cost: fuelCost,
                other_cost: otherCost,
                delivery_fee: fee,
                notes: (fm.tripNotes || "").trim() || false,
            };
            const created = await this.orm.create("isange.vehicle.trip", [vals]);
            const tripId = Array.isArray(created) ? created[0] : created;
            if (fm.tripState === "in_transit") {
                await this.orm.call("isange.vehicle.trip", "action_start_trip", [[tripId]]);
            } else if (fm.tripState === "completed") {
                await this.orm.call("isange.vehicle.trip", "action_complete_trip", [[tripId]]);
            }
            fm.showNewTrip = false;
            await this.loadData();
            this.notification.add(
                `${veh.plate} → ${destination} logged (Income ${this.fmt(fee)} · Cost ${this.fmt(fuelCost + otherCost)} ${this.state.currency}).`,
                { title: "Travel Recorded", type: "success" }
            );
        } catch (err) {
            console.error(err);
            this.notification.add("Could not record the travel. Check fields and try again.", { type: "danger" });
        } finally {
            fm.busy = false;
        }
    }

    async advanceTripState(tripId, action) {
        const fm = this.state.fleetMgr;
        if (fm.busy) {
            return;
        }
        fm.busy = true;
        try {
            const method = action === "start" ? "action_start_trip" : "action_complete_trip";
            await this.orm.call("isange.vehicle.trip", method, [[tripId]]);
            await this.loadData();
            this.notification.add(action === "start" ? "Trip marked On the Road." : "Trip completed and odometer updated.", {
                title: "Trip Updated",
                type: "success",
            });
        } catch (err) {
            console.error(err);
            this.notification.add("Could not update trip status.", { type: "danger" });
        } finally {
            fm.busy = false;
        }
    }

    openNewServiceForm() {
        const fm = this.state.fleetMgr;
        const veh = this.selectedFleetVehicle;
        fm.tab = "maintenance";
        fm.showNewTrip = false;
        fm.showNewService = !fm.showNewService;
        if (fm.showNewService && veh) {
            fm.svcDate = DateTime.local().toISODate();
            fm.svcTypeId = fm.serviceTypes.length ? fm.serviceTypes[0].id : false;
            fm.svcAmount = "";
            fm.svcOdometer = String(veh.odometer || 0);
            fm.svcVendorId = false;
            fm.svcDescription = "";
            fm.svcNotes = "";
            fm.svcState = "done";
        }
    }

    setSvcTypeId(id) {
        this.state.fleetMgr.svcTypeId = Number(id) || false;
    }

    setSvcVendorId(val) {
        this.state.fleetMgr.svcVendorId = Number(val) || false;
    }

    setSvcState(st) {
        this.state.fleetMgr.svcState = st;
    }

    async saveQuickService() {
        const fm = this.state.fleetMgr;
        const veh = this.selectedFleetVehicle;
        if (!veh || fm.busy) {
            return;
        }
        if (!fm.svcTypeId) {
            this.notification.add("Select a maintenance service type first.", { type: "warning" });
            return;
        }
        const amount = Math.max(0, parseFloat(fm.svcAmount) || 0);
        if (amount <= 0) {
            this.notification.add("Enter a maintenance cost greater than 0.", { type: "warning" });
            return;
        }
        const odo = Math.max(0, parseFloat(fm.svcOdometer) || 0);
        fm.busy = true;
        try {
            const vals = {
                vehicle_id: veh.id,
                service_type_id: Number(fm.svcTypeId),
                date: fm.svcDate || DateTime.local().toISODate(),
                amount,
                odometer: odo,
                vendor_id: fm.svcVendorId ? Number(fm.svcVendorId) : false,
                purchaser_id: veh.driverId || false,
                description: (fm.svcDescription || "").trim() || false,
                notes: (fm.svcNotes || "").trim() || false,
                state: fm.svcState || "done",
            };
            await this.orm.create("fleet.vehicle.log.services", [vals]);
            if (odo > (veh.odometer || 0)) {
                await this.safe(this.orm.write("fleet.vehicle", [veh.id], { odometer: odo }), null);
            }
            fm.showNewService = false;
            await this.loadData();
            const typeObj = fm.serviceTypes.find((s) => s.id === Number(fm.svcTypeId));
            this.notification.add(
                `${veh.plate} · ${typeObj ? typeObj.name : "Maintenance"} logged (${this.fmt(amount)} ${this.state.currency}).`,
                { title: "Maintenance Recorded", type: "success" }
            );
        } catch (err) {
            console.error(err);
            this.notification.add("Could not log maintenance cost. Check details and try again.", { type: "danger" });
        } finally {
            fm.busy = false;
        }
    }

    async completeServiceLog(svcId) {
        const fm = this.state.fleetMgr;
        if (fm.busy) {
            return;
        }
        fm.busy = true;
        try {
            await this.orm.write("fleet.vehicle.log.services", [svcId], { state: "done" });
            await this.loadData();
            this.notification.add("Maintenance log marked completed.", { title: "Maintenance Updated", type: "success" });
        } catch (err) {
            console.error(err);
            this.notification.add("Could not update maintenance status.", { type: "danger" });
        } finally {
            fm.busy = false;
        }
    }

    async saveVehicleOdometer() {
        const fm = this.state.fleetMgr;
        const veh = this.selectedFleetVehicle;
        if (!veh || fm.busy) {
            return;
        }
        const odo = Math.max(0, parseFloat(fm.editOdo) || 0);
        fm.busy = true;
        try {
            await this.orm.write("fleet.vehicle", [veh.id], { odometer: odo });
            await this.loadData();
            this.notification.add(`${veh.plate} odometer updated to ${this.fmt(odo)} ${veh.odometerUnit}.`, {
                title: "Odometer Saved",
                type: "success",
            });
        } catch (err) {
            console.error(err);
            this.notification.add("Could not update vehicle odometer.", { type: "danger" });
        } finally {
            fm.busy = false;
        }
    }

    newVehicle() {
        this.newRecord("fleet.vehicle");
    }

    openFleetServices(vehicleId = null) {
        const domain = vehicleId ? [["vehicle_id", "=", vehicleId]] : [];
        this.openList("fleet.vehicle.log.services", "Vehicle Maintenance & Service Logs", domain);
    }

    openVehicleTrips(vehicleId) {
        this.openList("isange.vehicle.trip", "Vehicle Delivery & Operation Trips", [["vehicle_id", "=", vehicleId]]);
    }

    // ------------------------------------------------------------------
    // Stock & Inventory Management Actions
    // ------------------------------------------------------------------

    toggleStockManager(forceOpen = null) {
        const sm = this.state.stockMgr;
        const nextOpen = forceOpen !== null ? forceOpen : !sm.open;
        sm.open = nextOpen;
        if (nextOpen) {
            this.state.fastSale.open = false;
            this.state.fleetMgr.open = false;
        }
    }

    get filteredStockProducts() {
        const sm = this.state.stockMgr;
        const q = (sm.search || "").trim().toLowerCase();
        const rank = { out: 0, low: 1, ok: 2 };
        return this.state.fastSale.products
            .filter((p) => {
                if (sm.filter === "low" && p.stockState === "ok") {
                    return false;
                }
                if (sm.filter === "out" && p.stockState !== "out") {
                    return false;
                }
                if (q && !(p.name || "").toLowerCase().includes(q) && !(p.categoryLabel || "").toLowerCase().includes(q)) {
                    return false;
                }
                return true;
            })
            .sort((a, b) => rank[a.stockState] - rank[b.stockState] || a.name.localeCompare(b.name));
    }

    get lowStockProducts() {
        return this.state.fastSale.products.filter((p) => p.stockState !== "ok");
    }

    async validateOrderPickings(pickingIds) {
        if (!Array.isArray(pickingIds) || !pickingIds.length) {
            return;
        }
        for (const pid of pickingIds) {
            try {
                const [pick] = await this.orm.read("stock.picking", [pid], ["id", "state", "move_ids"]);
                if (!pick || pick.state === "done" || pick.state === "cancel") {
                    continue;
                }
                if (pick.move_ids && pick.move_ids.length) {
                    const moves = await this.orm.read("stock.move", pick.move_ids, ["id", "product_uom_qty"]);
                    for (const m of moves) {
                        await this.orm.write("stock.move", [m.id], {
                            quantity: m.product_uom_qty,
                            picked: true,
                        });
                    }
                }
                await this.orm.call("stock.picking", "button_validate", [[pid]], {
                    context: {
                        skip_backorder: true,
                        picking_ids_not_to_backorder: [pid],
                    },
                });
            } catch (e) {
                console.warn("Could not auto-validate picking", pid, e);
            }
        }
    }

    async applyProductStock(productId, targetQty, reason = "") {
        const locId = this.state.stockMgr.locationId || 5;
        const newQty = Math.max(0, Number(targetQty) || 0);
        await this.safe(this.orm.write("product.product", [productId], { is_storable: true }), null);
        const existing = await this.orm.search("stock.quant", [
            ["product_id", "=", productId],
            ["location_id", "=", locId],
        ]);
        let quantId;
        if (existing.length) {
            quantId = existing[0];
            await this.orm.write("stock.quant", [quantId], { inventory_quantity: newQty });
        } else {
            const created = await this.orm.create("stock.quant", [
                {
                    product_id: productId,
                    location_id: locId,
                    inventory_quantity: newQty,
                },
            ]);
            quantId = Array.isArray(created) ? created[0] : created;
        }
        // `inventory_name` becomes the stock move name, so the reason shows in Odoo's history.
        await this.orm.call("stock.quant", "action_apply_inventory", [[quantId]], {
            context: reason ? { inventory_name: reason } : {},
        });
    }

    // ---- Adjust Stock panel ----

    get stockSelected() {
        const id = this.state.stockMgr.selectedId;
        return id ? this.state.fastSale.products.find((p) => p.id === id) || null : null;
    }

    get adjustReasons() {
        return ADJUST_REASONS[this.state.stockMgr.mode] || [];
    }

    get adjustQtyValue() {
        const v = parseFloat(this.state.stockMgr.qty);
        return isNaN(v) ? null : v;
    }

    get adjustDelta() {
        const prod = this.stockSelected;
        const v = this.adjustQtyValue;
        if (!prod || v === null) {
            return 0;
        }
        const cur = Number(prod.qty) || 0;
        const mode = this.state.stockMgr.mode;
        return mode === "add" ? v : mode === "remove" ? -v : v - cur;
    }

    get adjustAfter() {
        const prod = this.stockSelected;
        return prod ? (Number(prod.qty) || 0) + this.adjustDelta : 0;
    }

    get adjustError() {
        const prod = this.stockSelected;
        const v = this.adjustQtyValue;
        if (!prod || v === null) {
            return "";
        }
        const cur = Number(prod.qty) || 0;
        const mode = this.state.stockMgr.mode;
        if (v < 0) {
            return "Quantity cannot be negative.";
        }
        if (mode === "remove" && v > cur) {
            return `Only ${this.fmt(cur)} ${prod.uom} on hand — you cannot remove more than that.`;
        }
        return "";
    }

    get adjustReady() {
        const v = this.adjustQtyValue;
        if (v === null || this.adjustError) {
            return false;
        }
        return this.state.stockMgr.mode === "count" ? this.adjustDelta !== 0 : v > 0;
    }

    get selectedMarginAmt() {
        const prod = this.stockSelected;
        if (!prod) {
            return 0;
        }
        return (Number(prod.editPrice) || 0) - (Number(prod.editCost) || 0);
    }

    get selectedMarginPct() {
        const prod = this.stockSelected;
        if (!prod) {
            return 0;
        }
        return this.pct(this.selectedMarginAmt, Number(prod.editPrice) || 0);
    }

    setVehicleId(val) {
        this.state.fastSale.vehicleId = Number(val) || false;
    }

    setSupplierId(val) {
        this.state.stockMgr.supplierId = Number(val) || false;
    }

    async selectStockProduct(prod) {
        const sm = this.state.stockMgr;
        sm.selectedId = prod.id;
        sm.tab = "adjust";
        sm.mode = "add";
        sm.reason = ADJUST_REASONS.add[0].key;
        sm.qty = "";
        sm.note = "";
        sm.usePO = false;
        await this.loadStockHistory(prod.id);
    }

    closeStockPanel() {
        this.state.stockMgr.selectedId = false;
    }

    setAdjustMode(mode) {
        const sm = this.state.stockMgr;
        sm.mode = mode;
        sm.reason = ADJUST_REASONS[mode][0].key;
        sm.qty = mode === "count" && this.stockSelected ? String(this.stockSelected.qty) : "";
        sm.usePO = false;
    }

    selectAdjustReason(key) {
        const sm = this.state.stockMgr;
        sm.reason = key;
        if (key !== "delivery") {
            sm.usePO = false;
        }
    }

    bumpAdjustQty(step) {
        const sm = this.state.stockMgr;
        const next = Math.max(0, (this.adjustQtyValue || 0) + step);
        sm.qty = String(next);
    }

    async loadStockHistory(productId) {
        const sm = this.state.stockMgr;
        sm.historyLoading = true;
        const lines = await this.safe(
            this.orm.searchRead(
                "stock.move.line",
                [
                    ["product_id", "=", productId],
                    ["state", "=", "done"],
                ],
                ["date", "quantity", "location_id", "location_dest_id", "reference"],
                { order: "date desc, id desc", limit: 12 }
            ),
            []
        );
        sm.history = lines.map((l) => {
            const incoming = m2oName(l.location_dest_id).includes("WH/Stock") || m2oName(l.location_dest_id).endsWith("/Stock");
            const qty = Number(l.quantity) || 0;
            const when = toLocal(l.date);
            return {
                id: l.id,
                incoming,
                signed: incoming ? qty : -qty,
                label: l.reference || (incoming ? "Stock in" : "Stock out"),
                when: when ? when.toFormat("dd LLL, HH:mm") : "",
            };
        });
        sm.historyLoading = false;
    }

    async applyAdjustment() {
        const sm = this.state.stockMgr;
        const prod = this.stockSelected;
        if (!prod || sm.busy || !this.adjustReady) {
            return;
        }
        const reasonDef = this.adjustReasons.find((r) => r.key === sm.reason) || this.adjustReasons[0];
        const note = (sm.note || "").trim();
        const label = reasonDef.label + (note ? ` — ${note}` : "");
        const before = Number(prod.qty) || 0;
        const delta = this.adjustDelta;
        const after = this.adjustAfter;
        sm.busy = true;
        try {
            let usedFastRpc = false;
            try {
                await this.orm.call("isange.property", "execute_stock_adjustment", [
                    {
                        product_id: prod.id,
                        location_id: sm.locationId || 5,
                        target_qty: after,
                        delta,
                        label,
                        use_po: sm.mode === "add" && sm.reason === "delivery" && sm.usePO && Boolean(sm.supplierId),
                        supplier_id: sm.supplierId || 0,
                        unit_cost: Number(prod.editCost) || Number(prod.cost) || 0,
                    },
                ]);
                usedFastRpc = true;
            } catch (fastErr) {
                if (String(fastErr && fastErr.message || "").includes("execute_stock_adjustment")) {
                    usedFastRpc = false;
                } else {
                    throw fastErr;
                }
            }

            if (!usedFastRpc) {
                if (sm.mode === "add" && sm.reason === "delivery" && sm.usePO && sm.supplierId) {
                    const poCreated = await this.orm.create("purchase.order", [
                        {
                            partner_id: Number(sm.supplierId),
                            order_line: [
                                [
                                    0,
                                    0,
                                    {
                                        product_id: prod.id,
                                        name: prod.name,
                                        product_qty: delta,
                                        price_unit: Number(prod.editCost) || Number(prod.cost) || 0,
                                    },
                                ],
                            ],
                        },
                    ]);
                    const poId = Array.isArray(poCreated) ? poCreated[0] : poCreated;
                    await this.orm.call("purchase.order", "button_confirm", [[poId]]);
                    const [poInfo] = await this.orm.read("purchase.order", [poId], ["name", "picking_ids"]);
                    if (poInfo && poInfo.picking_ids && poInfo.picking_ids.length) {
                        await this.validateOrderPickings(poInfo.picking_ids);
                    } else {
                        await this.applyProductStock(prod.id, after, label);
                    }
                } else {
                    await this.applyProductStock(prod.id, after, label);
                }
            }
            sm.qty = "";
            sm.note = "";
            await Promise.all([this.loadData(), this.loadStockHistory(prod.id)]);
            const sign = delta > 0 ? "+" : "";
            this.notification.add(
                `${prod.name}: ${this.fmt(before)} → ${this.fmt(after)} ${prod.uom} (${sign}${this.fmt(delta)}) · ${reasonDef.label}`,
                { title: "Stock adjusted", type: "success" }
            );
        } catch (err) {
            console.error(err);
            this.notification.add("Could not apply the stock adjustment.", { type: "danger" });
        } finally {
            sm.busy = false;
        }
    }

    async saveProductPrices(product) {
        const sm = this.state.stockMgr;
        if (sm.busy) {
            return;
        }
        sm.busy = true;
        try {
            const cost = Math.max(0, Number(product.editCost) || 0);
            const price = Math.max(0, Number(product.editPrice) || 0);
            await this.orm.write("product.product", [product.id], {
                standard_price: cost,
                list_price: price,
                is_storable: true,
            });
            await this.loadData();
            this.notification.add(
                `${product.name} updated: Cost ${this.fmt(cost)} / Sell ${this.fmt(price)} ${this.state.currency}.`,
                { title: "Pricing Saved", type: "success" }
            );
        } catch (err) {
            console.error(err);
            this.notification.add("Could not save product prices.", { type: "danger" });
        } finally {
            sm.busy = false;
        }
    }

    async createStockItem() {
        const sm = this.state.stockMgr;
        const name = (sm.newName || "").trim();
        if (!name) {
            this.notification.add("Enter the product name first.", { type: "warning" });
            return;
        }
        const cost = Math.max(0, parseFloat(sm.newCost) || 0);
        const price = Math.max(0, parseFloat(sm.newPrice) || 0);
        const qty = Math.max(0, parseFloat(sm.newQty) || 0);
        sm.busy = true;
        try {
            const created = await this.orm.create("product.product", [
                {
                    name,
                    standard_price: cost,
                    list_price: price,
                    sale_ok: true,
                    purchase_ok: true,
                    type: "consu",
                    is_storable: true,
                    is_construction_item: true,
                    construction_category: sm.newCategory || "tools_hardware",
                },
            ]);
            const newId = Array.isArray(created) ? created[0] : created;
            if (qty > 0) {
                await this.applyProductStock(newId, qty);
            }
            sm.newName = "";
            sm.newCost = "";
            sm.newPrice = "";
            sm.newQty = "";
            sm.showNewProduct = false;
            await this.loadData();
            this.notification.add(`"${name}" created with ${qty} units in stock.`, {
                title: "Product Added to Inventory",
                type: "success",
            });
        } catch (err) {
            console.error(err);
            this.notification.add("Could not create product.", { type: "danger" });
        } finally {
            sm.busy = false;
        }
    }

    // ------------------------------------------------------------------
    // Fast 20-Second Hardware Sale Actions
    // ------------------------------------------------------------------

    toggleFastSale(forceOpen = null) {
        const fs = this.state.fastSale;
        const nextOpen = forceOpen !== null ? forceOpen : !fs.open;
        fs.open = nextOpen;
        if (nextOpen) {
            this.state.stockMgr.open = false;
            this.state.fleetMgr.open = false;
        }
    }

    get filteredFastProducts() {
        const fs = this.state.fastSale;
        const q = (fs.search || "").trim().toLowerCase();
        return fs.products.filter((p) => {
            if (fs.category !== "all" && p.category !== fs.category) {
                return false;
            }
            if (q && !(p.name || "").toLowerCase().includes(q) && !(p.uom || "").toLowerCase().includes(q)) {
                return false;
            }
            return true;
        });
    }

    cartQtyFor(productId) {
        const line = this.state.fastSale.cart.find((c) => c.id === productId);
        return line ? line.qty : 0;
    }

    addToCart(product, delta = 1) {
        const fs = this.state.fastSale;
        const existing = fs.cart.find((c) => c.id === product.id);
        if (existing) {
            existing.qty = Math.max(1, (Number(existing.qty) || 0) + delta);
        } else {
            fs.cart.push({
                id: product.id,
                name: product.name,
                uom: product.uom,
                qty: Math.max(1, delta),
                price: product.price || 0,
                originalPrice: product.price || 0,
                taxIds: product.taxIds || [],
                imageUrl: product.imageUrl || `/web/image/product.product/${product.id}/image_128`,
            });
        }
    }

    updateCartQty(productId, delta) {
        const fs = this.state.fastSale;
        const idx = fs.cart.findIndex((c) => c.id === productId);
        if (idx === -1) {
            return;
        }
        const next = (Number(fs.cart[idx].qty) || 0) + delta;
        if (next <= 0) {
            fs.cart.splice(idx, 1);
        } else {
            fs.cart[idx].qty = next;
        }
    }

    setCartQty(productId, rawVal) {
        const fs = this.state.fastSale;
        const line = fs.cart.find((c) => c.id === productId);
        if (!line) {
            return;
        }
        const num = parseFloat(rawVal);
        line.qty = isNaN(num) || num <= 0 ? 1 : num;
    }

    setCartPrice(productId, rawVal) {
        const fs = this.state.fastSale;
        const line = fs.cart.find((c) => c.id === productId);
        if (!line) {
            return;
        }
        const num = parseFloat(rawVal);
        line.price = isNaN(num) || num < 0 ? 0 : num;
    }

    removeFromCart(productId) {
        const fs = this.state.fastSale;
        const idx = fs.cart.findIndex((c) => c.id === productId);
        if (idx !== -1) {
            fs.cart.splice(idx, 1);
        }
    }

    clearCart() {
        const fs = this.state.fastSale;
        fs.cart = [];
        fs.addDelivery = false;
        fs.deliveryFee = 0;
        fs.splitOpen = false;
        fs.split = { cash: 0, momo: 0, bank: 0 };
    }

    toggleSplitPayment() {
        const fs = this.state.fastSale;
        fs.splitOpen = !fs.splitOpen;
        if (fs.splitOpen) {
            const total = this.fastCartTotal;
            const currentSum = (Number(fs.split.cash) || 0) + (Number(fs.split.momo) || 0) + (Number(fs.split.bank) || 0);
            if (total > 0 && currentSum !== total) {
                const half = Math.round(total / 2);
                fs.split.cash = half;
                fs.split.momo = total - half;
                fs.split.bank = 0;
            }
        }
    }

    setSplitAmount(method, rawVal) {
        const fs = this.state.fastSale;
        const val = Math.max(0, parseFloat(rawVal) || 0);
        fs.split[method] = val;
        // If user edits Cash while Bank is 0, auto-balance MoMo to the remaining total for 1-step 2-way splits
        const total = this.fastCartTotal;
        if (method === "cash" && (Number(fs.split.bank) || 0) === 0 && total > 0) {
            fs.split.momo = Math.max(0, total - val);
        }
    }

    fillSplitRemainder(method) {
        const fs = this.state.fastSale;
        const total = this.fastCartTotal;
        const others = ["cash", "momo", "bank"]
            .filter((k) => k !== method)
            .reduce((acc, k) => acc + (Number(fs.split[k]) || 0), 0);
        fs.split[method] = Math.max(0, total - others);
    }

    clearSplitMethod(method) {
        this.state.fastSale.split[method] = 0;
    }

    get fastSplitPaid() {
        const s = this.state.fastSale.split;
        return (Number(s.cash) || 0) + (Number(s.momo) || 0) + (Number(s.bank) || 0);
    }

    get fastSplitRemaining() {
        return this.fastCartTotal - this.fastSplitPaid;
    }

    selectFastCustomer(partnerId) {
        this.state.fastSale.partnerId = Number(partnerId);
        this.state.fastSale.showNewCustomer = false;
    }

    async createQuickCustomer() {
        const fs = this.state.fastSale;
        const name = (fs.newCustomerName || "").trim();
        if (!name) {
            this.notification.add("Enter the customer's name first.", { type: "warning" });
            return false;
        }
        const phone = (fs.newCustomerPhone || "").trim();
        try {
            const created = await this.orm.create("res.partner", [{ name, phone: phone || false, city: "Kigali" }]);
            const newId = Array.isArray(created) ? created[0] : created;
            const entry = { id: newId, name, shortName: name, phone, city: "Kigali" };
            fs.customers.splice(1, 0, entry);
            fs.partnerId = newId;
            fs.newCustomerName = "";
            fs.newCustomerPhone = "";
            fs.showNewCustomer = false;
            this.notification.add(`Customer "${name}" added and selected.`, { type: "success" });
            return newId;
        } catch (err) {
            console.error(err);
            this.notification.add("Could not create customer.", { type: "danger" });
            return false;
        }
    }

    async createQuickItem() {
        const fs = this.state.fastSale;
        const name = (fs.newItemName || "").trim();
        const price = parseFloat(fs.newItemPrice) || 0;
        if (!name) {
            this.notification.add("Enter the item name first.", { type: "warning" });
            return;
        }
        try {
            const created = await this.orm.create("product.product", [
                {
                    name,
                    list_price: price,
                    sale_ok: true,
                    purchase_ok: true,
                    type: "consu",
                    is_storable: true,
                    is_construction_item: true,
                    construction_category: fs.category !== "all" ? fs.category : "tools_hardware",
                },
            ]);
            const newId = Array.isArray(created) ? created[0] : created;
            const prod = {
                id: newId,
                name,
                price,
                uom: "Units",
                category: fs.category !== "all" ? fs.category : "tools_hardware",
                categoryLabel: "Hardware",
                taxIds: fs.vatTaxId ? [fs.vatTaxId] : [],
                imageUrl: `/web/image/product.product/${newId}/image_256`,
            };
            fs.products.unshift(prod);
            this.addToCart(prod, 1);
            fs.newItemName = "";
            fs.newItemPrice = "";
            fs.showNewItem = false;
            this.notification.add(`"${name}" created and added to the sale!`, { type: "success" });
        } catch (err) {
            console.error(err);
            this.notification.add("Could not create new item.", { type: "danger" });
        }
    }

    get fastCartSubtotal() {
        let sum = 0;
        for (const item of this.state.fastSale.cart) {
            sum += (Number(item.qty) || 0) * (Number(item.price) || 0);
        }
        return sum;
    }

    get fastCartVat() {
        const fs = this.state.fastSale;
        if (!fs.applyVat) {
            return 0;
        }
        return Math.round(this.fastCartSubtotal * ((fs.vatRate || 18) / 100));
    }

    get fastCartTotal() {
        const delivery = this.state.fastSale.addDelivery ? Number(this.state.fastSale.deliveryFee) || 0 : 0;
        return this.fastCartSubtotal + this.fastCartVat + delivery;
    }

    async completeFastSale(mode = "cash") {
        const fs = this.state.fastSale;
        if (fs.busy) {
            return;
        }
        if (!fs.cart.length) {
            this.notification.add("Tap one or more items on the left to add them to the sale first.", {
                title: "Cart is empty",
                type: "warning",
            });
            return;
        }

        fs.busy = true;
        try {
            let partnerId = Number(fs.partnerId);
            if (!partnerId && fs.customers.length) {
                partnerId = fs.customers[0].id;
            }
            const taxIds = fs.applyVat && fs.vatTaxId ? [fs.vatTaxId] : [];
            const totalSnapshot = this.fastCartTotal;
            const shouldPrint = fs.autoPrint;

            // Fast path: execute entire sale (Order + Stock + Invoice + Payment + Trip) in 1 RPC
            let fastRes = null;
            try {
                fastRes = await this.orm.call("isange.property", "execute_fast_sale", [
                    {
                        mode,
                        partner_id: partnerId,
                        new_customer_name: fs.showNewCustomer ? (fs.newCustomerName || "").trim() : "",
                        new_customer_phone: fs.showNewCustomer ? (fs.newCustomerPhone || "").trim() : "",
                        cart: fs.cart.map((item) => ({
                            id: item.id,
                            name: item.name,
                            qty: Number(item.qty) || 1,
                            price: Number(item.price) || 0,
                            original_price: Number(item.originalPrice) || 0,
                        })),
                        tax_ids: taxIds,
                        invoice_date: DateTime.local().toISODate(),
                        journals: fs.journals,
                        split: fs.split,
                        total_amount: totalSnapshot,
                        add_delivery: Boolean(fs.addDelivery && fs.vehicleId),
                        vehicle_id: fs.vehicleId || 0,
                        destination: (fs.destination || "").trim(),
                        delivery_fee: Number(fs.deliveryFee) || 0,
                    },
                ]);
            } catch (fastErr) {
                if (String(fastErr && fastErr.message || "").includes("execute_fast_sale")) {
                    fastRes = null;
                } else {
                    throw fastErr;
                }
            }

            if (fastRes && fastRes.so_id) {
                if (fs.showNewCustomer && (fs.newCustomerName || "").trim()) {
                    fs.newCustomerName = "";
                    fs.newCustomerPhone = "";
                    fs.showNewCustomer = false;
                    fs.partnerId = fastRes.partner_id;
                }
                fs.lastSale = {
                    soId: fastRes.so_id,
                    soName: fastRes.so_name,
                    invId: fastRes.inv_id || false,
                    invName: fastRes.inv_name || "",
                    customer: fastRes.customer || "Customer",
                    amount: totalSnapshot,
                    badge: fastRes.badge,
                };
                this.clearCart();
                // Non-blocking background refresh so cashier can ring up the next customer immediately!
                this.loadData({ background: true });
                if (mode === "quotation") {
                    this.notification.add(`Quotation ${fastRes.so_name} saved! Opening Pro-Forma PDF...`, {
                        title: "Quotation Ready",
                        type: "success",
                    });
                    this.printProForma(fastRes.so_id);
                } else {
                    this.notification.add(
                        `${fastRes.so_name} (${fastRes.inv_name}) — ${this.fmt(totalSnapshot)} ${this.state.currency} [${fastRes.badge}]`,
                        { title: "Sale Completed!", type: "success" }
                    );
                    if (shouldPrint && fastRes.inv_id) {
                        this.printInvoicePdf(fastRes.inv_id);
                    }
                }
                return;
            }

            if (fs.showNewCustomer && (fs.newCustomerName || "").trim()) {
                const createdId = await this.createQuickCustomer();
                if (createdId) {
                    partnerId = createdId;
                }
            }

            // Remember any prices typed for items that previously had 0 list_price
            for (const item of fs.cart) {
                if (!item.originalPrice && item.price > 0) {
                    this.safe(this.orm.write("product.product", [item.id], { list_price: Number(item.price) }), null);
                    const catProd = fs.products.find((p) => p.id === item.id);
                    if (catProd) {
                        catProd.price = Number(item.price);
                    }
                }
            }

            const orderLines = fs.cart.map((item) => [
                0,
                0,
                {
                    product_id: item.id,
                    name: item.name,
                    product_uom_qty: Number(item.qty) || 1,
                    price_unit: Number(item.price) || 0,
                    tax_ids: [[6, 0, taxIds]],
                },
            ]);

            const soCreated = await this.orm.create("sale.order", [
                {
                    partner_id: partnerId,
                    order_line: orderLines,
                },
            ]);
            const soId = Array.isArray(soCreated) ? soCreated[0] : soCreated;

            const [soInfo] = await this.orm.read("sale.order", [soId], ["name", "order_line", "amount_total"]);
            const partnerObj = fs.customers.find((c) => c.id === partnerId);
            const customerName = partnerObj ? partnerObj.shortName : "Customer";

            // Mode 1: Quotation / Pro-Forma Only
            if (mode === "quotation") {
                fs.lastSale = {
                    soId,
                    soName: soInfo.name,
                    invId: false,
                    invName: "",
                    customer: customerName,
                    amount: this.fastCartTotal,
                    badge: "Quotation / Pro-Forma",
                };
                this.clearCart();
                await this.loadData();
                this.notification.add(`Quotation ${soInfo.name} saved! Opening Pro-Forma PDF...`, {
                    title: "Quotation Ready",
                    type: "success",
                });
                this.printProForma(soId);
                return;
            }

            // Confirm the Sales Order & automatically validate its outgoing stock delivery
            await this.orm.call("sale.order", "action_confirm", [[soId]]);
            const [confirmedSo] = await this.orm.read("sale.order", [soId], ["picking_ids"]);
            if (confirmedSo && confirmedSo.picking_ids && confirmedSo.picking_ids.length) {
                await this.validateOrderPickings(confirmedSo.picking_ids);
            }

            // Create and post the Customer Invoice linked to the Sales Order lines
            const soLines = await this.orm.read("sale.order.line", soInfo.order_line, [
                "id",
                "product_id",
                "name",
                "product_uom_qty",
                "price_unit",
                "tax_ids",
            ]);
            const invLines = soLines.map((l) => [
                0,
                0,
                {
                    product_id: m2oId(l.product_id),
                    name: l.name,
                    quantity: l.product_uom_qty,
                    price_unit: l.price_unit,
                    tax_ids: [[6, 0, l.tax_ids || []]],
                    sale_line_ids: [[6, 0, [l.id]]],
                },
            ]);

            const invCreated = await this.orm.create("account.move", [
                {
                    move_type: "out_invoice",
                    partner_id: partnerId,
                    invoice_date: DateTime.local().toISODate(),
                    invoice_origin: soInfo.name,
                    invoice_line_ids: invLines,
                },
            ]);
            const invId = Array.isArray(invCreated) ? invCreated[0] : invCreated;
            await this.orm.call("account.move", "action_post", [[invId]]);
            const [invInfo] = await this.orm.read("account.move", [invId], ["name", "amount_total"]);

            let paymentBadge = "Credit (Unpaid Invoice)";
            if (mode === "cash" || mode === "momo" || mode === "bank") {
                const journalId = fs.journals[mode] || fs.journals.cash || fs.journals.momo || fs.journals.bank;
                if (journalId) {
                    const payCtx = { active_model: "account.move", active_ids: [invId] };
                    const wizCreated = await this.orm.create("account.payment.register", [{ journal_id: journalId }], {
                        context: payCtx,
                    });
                    const wizId = Array.isArray(wizCreated) ? wizCreated[0] : wizCreated;
                    await this.orm.call("account.payment.register", "action_create_payments", [[wizId]], {
                        context: payCtx,
                    });
                }
                paymentBadge = mode === "cash" ? "Paid — Cash" : mode === "momo" ? "Paid — MTN MoMo" : "Paid — Bank";
            } else if (mode === "split") {
                const legs = [
                    { key: "cash", label: "Cash", amount: Math.max(0, Number(fs.split.cash) || 0) },
                    { key: "momo", label: "MoMo", amount: Math.max(0, Number(fs.split.momo) || 0) },
                    { key: "bank", label: "Bank", amount: Math.max(0, Number(fs.split.bank) || 0) },
                ].filter((l) => l.amount > 0);

                const payCtx = { active_model: "account.move", active_ids: [invId] };
                for (const leg of legs) {
                    const journalId = fs.journals[leg.key] || fs.journals.cash || fs.journals.momo || fs.journals.bank;
                    if (journalId) {
                        const wizCreated = await this.orm.create(
                            "account.payment.register",
                            [
                                {
                                    journal_id: journalId,
                                    amount: leg.amount,
                                    payment_difference_handling: "open",
                                },
                            ],
                            { context: payCtx }
                        );
                        const wizId = Array.isArray(wizCreated) ? wizCreated[0] : wizCreated;
                        await this.orm.call("account.payment.register", "action_create_payments", [[wizId]], {
                            context: payCtx,
                        });
                    }
                }
                if (legs.length) {
                    const parts = legs.map((l) => `${l.label} ${this.fmtShort(l.amount)}`).join(" + ");
                    const rem = this.fastCartTotal - legs.reduce((a, l) => a + l.amount, 0);
                    paymentBadge = rem > 0 ? `Partial — ${parts} (Due ${this.fmtShort(rem)})` : `Split — ${parts}`;
                }
            }

            // Optional 1-click truck delivery trip
            if (fs.addDelivery && fs.vehicleId) {
                const vehicle = this.state.vehicles.find((v) => v.id === Number(fs.vehicleId));
                const driverId = (vehicle && vehicle.driverId) || partnerId;
                const cargo = fs.cart.map((c) => `${c.qty}x ${c.name}`).join(", ");
                await this.safe(
                    this.orm.create("isange.vehicle.trip", [
                        {
                            vehicle_id: Number(fs.vehicleId),
                            driver_id: driverId,
                            trip_type: "delivery",
                            sale_order_id: soId,
                            partner_id: partnerId,
                            destination_location: (fs.destination || "").trim() || `${customerName} Site, Kigali`,
                            cargo_description: cargo,
                            delivery_fee: Number(fs.deliveryFee) || 0,
                            state: "in_transit",
                        },
                    ]),
                    null
                );
            }

            fs.lastSale = {
                soId,
                soName: soInfo.name,
                invId,
                invName: invInfo.name,
                customer: customerName,
                amount: totalSnapshot,
                badge: paymentBadge,
            };

            this.clearCart();
            await this.loadData();

            this.notification.add(
                `${soInfo.name} (${invInfo.name}) — ${this.fmt(totalSnapshot)} ${this.state.currency} [${paymentBadge}]`,
                {
                    title: "Sale Completed!",
                    type: "success",
                }
            );

            if (shouldPrint && invId) {
                this.printInvoicePdf(invId);
            }
        } catch (error) {
            console.error("Fast Sale error:", error);
            this.notification.add("Could not complete the sale. Check the items and try again.", {
                title: "Sale Error",
                type: "danger",
            });
        } finally {
            fs.busy = false;
        }
    }

    printInvoicePdf(invId) {
        if (!invId) {
            return;
        }
        this.actionService.doAction({
            type: "ir.actions.report",
            report_type: "qweb-pdf",
            report_name: "account.report_invoice_with_payments",
            report_file: "account.report_invoice_with_payments",
            context: {
                active_id: invId,
                active_ids: [invId],
                active_model: "account.move",
            },
        });
    }

    printProForma(soId) {
        if (!soId) {
            return;
        }
        this.actionService.doAction({
            type: "ir.actions.report",
            report_type: "qweb-pdf",
            report_name: "sale.report_saleorder_pro_forma",
            report_file: "sale.report_saleorder_pro_forma",
            context: {
                active_id: soId,
                active_ids: [soId],
                active_model: "sale.order",
            },
        });
    }

    // ------------------------------------------------------------------
    // Formatting helpers (used in the template)
    // ------------------------------------------------------------------

    fmt(value) {
        return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(Math.round(value || 0));
    }

    fmtShort(value) {
        const v = value || 0;
        const abs = Math.abs(v);
        if (abs >= 1000000000) {
            return `${(v / 1000000000).toFixed(1)}B`;
        }
        if (abs >= 1000000) {
            return `${(v / 1000000).toFixed(1)}M`;
        }
        if (abs >= 1000) {
            return `${(v / 1000).toFixed(0)}K`;
        }
        return `${Math.round(v)}`;
    }

    initials(name) {
        const clean = (name || "?").replace(/\(.*?\)/g, "").trim();
        const parts = clean.split(/\s+/).filter(Boolean);
        if (!parts.length) {
            return "?";
        }
        return ((parts[0][0] || "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
    }

    pct(part, total) {
        if (!total) {
            return 0;
        }
        return Math.round((part / total) * 100);
    }

    get occupancyDash() {
        const p = Math.max(0, Math.min(100, this.state.kpi.occupancyPct || 0));
        return `${p} ${100 - p}`;
    }

    get mixSegments() {
        const mix = this.state.mix;
        if (!mix.total) {
            return {
                hDash: "0 100",
                hOffset: 25,
                rDash: "0 100",
                rOffset: 25,
                fDash: "0 100",
                fOffset: 25,
            };
        }
        const h = (mix.hardware / mix.total) * 100;
        const r = (mix.rent / mix.total) * 100;
        const f = Math.max(0, 100 - h - r);
        return {
            hDash: `${h} ${100 - h}`,
            hOffset: 25,
            rDash: `${r} ${100 - r}`,
            rOffset: 25 - h,
            fDash: `${f} ${100 - f}`,
            fOffset: 25 - h - r,
        };
    }

    get occupancyRingStyle() {
        const p = this.state.kpi.occupancyPct;
        return `background: conic-gradient(#22c55e 0% ${p}%, rgba(148, 163, 184, 0.25) ${p}% 100%);`;
    }

    get mixDonutStyle() {
        const mix = this.state.mix;
        if (!mix.total) {
            return "background: conic-gradient(rgba(148, 163, 184, 0.25) 0% 100%);";
        }
        const h = (mix.hardware / mix.total) * 100;
        const r = h + (mix.rent / mix.total) * 100;
        return `background: conic-gradient(#3b82f6 0% ${h}%, #8b5cf6 ${h}% ${r}%, #f59e0b ${r}% 100%);`;
    }

    // ------------------------------------------------------------------
    // Accessibility: activate clickable cards / rows with Enter or Space
    // ------------------------------------------------------------------

    onKeyActivate(ev, callback) {
        if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            callback();
        }
    }

    // ------------------------------------------------------------------
    // Navigation
    // ------------------------------------------------------------------

    openRecord(model, id) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    newRecord(model, context = {}) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            views: [[false, "form"]],
            target: "current",
            context,
        });
    }

    openList(model, name, domain = [], viewMode = "list") {
        const views = viewMode === "kanban" ? [[false, "kanban"], [false, "list"], [false, "form"]] : [[false, "list"], [false, "form"]];
        this.actionService.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: model,
            views,
            domain,
            target: "current",
        });
    }

    openXmlAction(xmlId) {
        this.actionService.doAction(xmlId);
    }

    openSales() {
        this.openList("sale.order", "Confirmed Sales Orders", [["state", "in", ["sale", "done"]]]);
    }

    openPurchases() {
        this.openList("purchase.order", "Confirmed Purchases", [["state", "in", ["purchase", "done"]]]);
    }

    openUnits() {
        this.openXmlAction("isange_property_fleet.action_isange_property");
    }

    openLeases() {
        this.openXmlAction("isange_property_fleet.action_isange_lease");
    }

    openTrips() {
        this.openXmlAction("isange_property_fleet.action_isange_vehicle_trip");
    }

    openOutstandingRent() {
        this.openList("account.move", "Outstanding Rent Invoices", [
            ["isange_property_id", "!=", false],
            ["move_type", "=", "out_invoice"],
            ["state", "=", "posted"],
            ["payment_state", "not in", ["paid", "in_payment", "reversed"]],
        ]);
    }

    openVehicles() {
        this.openList("fleet.vehicle", "Company Vehicles", [], "kanban");
    }

    newQuotation() {
        this.newRecord("sale.order");
    }

    newPurchase() {
        this.newRecord("purchase.order");
    }

    newUnit() {
        this.newRecord("isange.property", { default_property_type: "commercial" });
    }

    newLease() {
        this.newRecord("isange.lease");
    }

    newTrip() {
        this.newRecord("isange.vehicle.trip");
    }

    newExpense() {
        this.newRecord("isange.property.expense");
    }

    openPos() {
        this.actionService.doAction("point_of_sale.action_pos_config_kanban").catch(() => {
            this.openList("pos.config", "Point of Sale", [], "kanban");
        });
    }

    async printBusinessReport() {
        const ids = await this.safe(this.orm.search("isange.property", [], { limit: 1 }), []);
        if (!ids.length) {
            this.notification.add("Add at least one building unit before printing the Business Report.", {
                title: "Nothing to print yet",
                type: "warning",
            });
            return;
        }
        this.actionService.doAction("isange_property_fleet.action_report_all_in_one_property", {
            additionalContext: {
                active_id: ids[0],
                active_ids: ids,
                active_model: "isange.property",
            },
        });
    }

    async refresh() {
        this.state.loading = true;
        await this.loadData();
    }
}

registry.category("actions").add("isange_command_center", IsangeDashboard, { force: true });
registry.category("actions").add("isange_fast_sale", IsangeDashboard, { force: true });
registry.category("actions").add("isange_stock_manager", IsangeDashboard, { force: true });
registry.category("actions").add("isange_fleet_manager", IsangeDashboard, { force: true });
