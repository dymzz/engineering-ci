# ECI #74 / Weaver 0.28.0 native Advice Policy.
# Narrow Conditionally Required gate for fixed SemConv v1.45.0 Pipeline/Task
# signals when result is failure|error and error.type is absent.
# Does not elevate all information findings; does not reimplement SemConv in Python.
package live_check_advice

import rego.v1

_pipeline_signal := "cicd.pipeline.run.server"

_task_signal := "cicd.pipeline.task.internal"

_trigger_results := {"failure", "error"}

_signal_id(group) := id if {
	id := group.id
	is_string(id)
	id != ""
} else := id if {
	id := group.type
	is_string(id)
	id != ""
}

_span_has_attr(span, key) if {
	some attr in span.attributes
	attr.name == key
}

_span_attr(span, key) := value if {
	some attr in span.attributes
	attr.name == key
	value := attr.value
}

# Pipeline Root: cicd.pipeline.result in {failure, error} requires error.type.
deny contains make_advice(advice_type, advice_level, advice_context, message) if {
	input.sample.span
	input.registry_group
	signal := _signal_id(input.registry_group)
	signal == _pipeline_signal
	result := _span_attr(input.sample.span, "cicd.pipeline.result")
	is_string(result)
	result in _trigger_results
	not _span_has_attr(input.sample.span, "error.type")
	advice_type := "cicd_conditional_error_type_required"
	advice_level := "violation"
	advice_context := {
		"attribute_key": "error.type",
		"signal": signal,
		"result": result,
		"result_attribute": "cicd.pipeline.result",
		"normative_source": "https://opentelemetry.io/docs/specs/semconv/cicd/cicd-spans/",
	}
	message := sprintf(
		"Conditionally required attribute 'error.type' is missing when cicd.pipeline.result is '%s' (signal %s)",
		[result, signal],
	)
}

# Task: cicd.pipeline.task.run.result in {failure, error} requires error.type.
deny contains make_advice(advice_type, advice_level, advice_context, message) if {
	input.sample.span
	input.registry_group
	signal := _signal_id(input.registry_group)
	signal == _task_signal
	result := _span_attr(input.sample.span, "cicd.pipeline.task.run.result")
	is_string(result)
	result in _trigger_results
	not _span_has_attr(input.sample.span, "error.type")
	advice_type := "cicd_conditional_error_type_required"
	advice_level := "violation"
	advice_context := {
		"attribute_key": "error.type",
		"signal": signal,
		"result": result,
		"result_attribute": "cicd.pipeline.task.run.result",
		"normative_source": "https://opentelemetry.io/docs/specs/semconv/cicd/cicd-spans/",
	}
	message := sprintf(
		"Conditionally required attribute 'error.type' is missing when cicd.pipeline.task.run.result is '%s' (signal %s)",
		[result, signal],
	)
}
