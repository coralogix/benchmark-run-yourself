package model

import "fmt"

// Label value pools. Sizes must match the
// fan-out constants in band.go.

var (
	clusters = [4]string{
		"eu-west-1-prod-a",
		"eu-west-1-prod-b",
		"eu-west-1-prod-c",
		"eu-west-1-prod-d",
	}
	regions = [4]string{
		"eu-west-1",
		"us-east-1",
		"us-west-2",
		"ap-southeast-1",
	}
	namespaces = [10]string{
		"checkout",
		"payments",
		"search",
		"catalogue",
		"shipping",
		"auth",
		"notifications",
		"media",
		"analytics",
		"frontend",
	}
	routes = [10]string{
		"/api/v1/cart",
		"/api/v1/checkout",
		"/api/v1/products",
		"/api/v1/search",
		"/api/v1/orders",
		"/api/v1/customers",
		"/api/v1/payments",
		"/api/v1/session",
		"/api/v1/wishlist",
		"/healthz",
	}
	methods  = [5]string{"GET", "POST", "PUT", "PATCH", "DELETE"}
	statuses = [4]string{"200", "301", "404", "500"}
	bucketLE = [12]string{
		"0.005", "0.01", "0.025", "0.05", "0.1", "0.25",
		"0.5", "1", "2.5", "5", "10", "+Inf",
	}
	containers = [4]string{"app", "sidecar", "proxy", "init"}
	customers  = [5]string{"cust-0000", "cust-0001", "cust-0002", "cust-0003", "cust-0004"}
	ownerKinds = [4]string{"ReplicaSet", "StatefulSet", "DaemonSet", "Job"}
)

// services builds 100 realistic microservice names: 20 base names x 5 kinds.
var services = buildServices()

func buildServices() [100]string {
	bases := [...]string{
		"cart", "catalogue", "checkout", "currency", "email",
		"frontend", "inventory", "loyalty", "newsletter", "order",
		"payment", "pricing", "product", "profile", "recommendation",
		"review", "search", "session", "shipping", "wishlist",
	}
	suffixes := [...]string{"api", "web", "worker", "svc", "batch"}
	var out [100]string
	i := 0
	for _, base := range bases {
		for _, suffix := range suffixes {
			out[i] = base + "-" + suffix
			i++
		}
	}
	return out
}

// mix64 is the splitmix64 finaliser: a cheap, stateless, deterministic hash
// used to give pods realistic-looking name fragments.
func mix64(x uint64) uint64 {
	x += 0x9E3779B97F4A7C15
	x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9
	x = (x ^ (x >> 27)) * 0x94D049BB133111EB
	return x ^ (x >> 31)
}

// PodsPerNode is how many pods share a node for node-name derivation.
const PodsPerNode = 10

// PodLabels carries every pod-level label. Node is only used by the info
// family, which is what forces the join.
type PodLabels struct {
	Cluster   string
	Namespace string
	Service   string
	Pod       string
	Region    string
	Node      string
}

// DerivePod derives all pod-level labels from podIndex by pure arithmetic.
// Nesting: a pod belongs to exactly one cluster, namespace,
// service, region and node. service to namespace to cluster and region are
// deterministic functions of the index; no label is stored.
//
//	service   = podIndex mod 100
//	base      = service / 5            (20 base names)
//	namespace = base mod 10            (pods of a service share a namespace)
//	cluster   = namespace mod 4        (each namespace lives in one cluster)
//	region    = base mod 4             (each service base is region-pinned)
//	node      = podIndex / 10          (~10 pods per node, info family only)
func DerivePod(podIndex int64) PodLabels {
	pi := uint64(podIndex)
	serviceIdx := pi % uint64(len(services))
	baseIdx := serviceIdx / 5
	namespaceIdx := baseIdx % 10
	clusterIdx := namespaceIdx % 4
	regionIdx := baseIdx % 4

	service := services[serviceIdx]
	nodeIdx := pi / PodsPerNode
	node := fmt.Sprintf(
		"ip-10-%d-%d-%d.%s.compute.internal",
		(nodeIdx>>16)&0xff,
		(nodeIdx>>8)&0xff,
		nodeIdx&0xff,
		regions[regionIdx],
	)

	return PodLabels{
		Cluster:   clusters[clusterIdx],
		Namespace: namespaces[namespaceIdx],
		Service:   service,
		Pod: fmt.Sprintf(
			"%s-%05x-%05x",
			service,
			mix64(pi*2+1)&0xFFFFF,
			mix64(pi*2+2)&0xFFFFF,
		),
		Region: regions[regionIdx],
		Node:   node,
	}
}

type Label struct {
	Name  string
	Value string
}

type Series struct {
	Name   string
	Labels []Label // fixed order: common labels first, then family-specific
}

// SeriesFor derives the series at seriesIndex within family f of band b,
// where 0 <= seriesIndex < f.SeriesCount(). Pure arithmetic: podIndex is the
// quotient of the index by the fan-out, the fan-out index is the remainder,
// and every label value is a lookup or a hash of those integers. No series
// is ever stored.
func SeriesFor(b Band, f Family, seriesIndex int64) Series {
	podIndex := seriesIndex / f.Fanout
	fan := seriesIndex % f.Fanout
	p := DerivePod(podIndex)

	labels := make([]Label, 0, 9)
	labels = append(labels,
		Label{"cluster", p.Cluster},
		Label{"namespace", p.Namespace},
		Label{"service", p.Service},
		Label{"pod", p.Pod},
		Label{"region", p.Region},
	)

	switch f.Kind {
	case FamilyCounter:
		route := (fan / (CounterMethods * CounterStatuses * CounterCustomers)) % CounterRoutes
		method := (fan / (CounterStatuses * CounterCustomers)) % CounterMethods
		status := (fan / CounterCustomers) % CounterStatuses
		cust := fan % CounterCustomers
		labels = append(labels,
			Label{"http_route", routes[route]},
			Label{"http_method", methods[method]},
			Label{"status_code", statuses[status]},
			Label{"customer_id", customers[cust]},
		)
		return Series{Name: f.Name, Labels: labels}

	case FamilyHistogram:
		route := fan / HistogramSeriesPerLabelSet
		sub := fan % HistogramSeriesPerLabelSet
		switch {
		case sub < HistogramBuckets:
			labels = append(labels,
				Label{"http_route", routes[route]},
				Label{"le", bucketLE[sub]},
			)
			return Series{Name: f.Name + "_bucket", Labels: labels}
		case sub == HistogramBuckets:
			labels = append(labels, Label{"http_route", routes[route]})
			return Series{Name: f.Name + "_sum", Labels: labels}
		default:
			labels = append(labels, Label{"http_route", routes[route]})
			return Series{Name: f.Name + "_count", Labels: labels}
		}

	case FamilyGauge:
		container := fan / GaugeCustomers
		cust := fan % GaugeCustomers
		labels = append(labels,
			Label{"container", containers[container]},
			Label{"customer_id", customers[cust]},
		)
		return Series{Name: f.Name, Labels: labels}

	case FamilyInfo:
		ni := uint64(podIndex)
		labels = append(labels,
			Label{"node", p.Node},
			Label{"image_tag", fmt.Sprintf(
				"v%d.%d.%d",
				mix64(ni*7+11)%4,
				mix64(ni*7+12)%10,
				mix64(ni*7+13)%10,
			)},
			Label{"owner_kind", ownerKinds[mix64(ni*7+14)%4]},
			Label{"created_by", fmt.Sprintf("%s-%05x", p.Service, mix64(ni*7+15)&0xFFFFF)},
		)
		return Series{Name: f.Name, Labels: labels}

	default:
		panic("unknown family kind " + string(f.Kind))
	}
}
