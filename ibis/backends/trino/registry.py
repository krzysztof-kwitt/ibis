import sqlalchemy as sa

import ibis.common.exceptions as com
import ibis.expr.operations as ops
from ibis.backends.base.sql.alchemy.datatypes import to_sqla_type
from ibis.backends.base.sql.alchemy.registry import (
    fixed_arity,
    reduction,
    sqlalchemy_operation_registry,
    sqlalchemy_window_functions_registry,
    unary,
)
from ibis.backends.postgres.registry import _corr, _covar

operation_registry = sqlalchemy_operation_registry.copy()
operation_registry.update(sqlalchemy_window_functions_registry)


def _arbitrary(t, op):
    if op.how == "heavy":
        raise ValueError('Trino does not support how="heavy"')
    return reduction(sa.func.arbitrary)(t, op)


def _json_get_item(t, op):
    arg = t.translate(op.arg)
    index = t.translate(op.index)
    fmt = "%d" if op.index.output_dtype.is_integer() else '"%s"'
    return sa.func.json_extract(arg, sa.func.format(f"$[{fmt}]", index))


def _group_concat(t, op):
    if not isinstance(op.sep, ops.Literal):
        raise com.IbisTypeError("Trino group concat separator must be a literal value")

    arg = sa.func.array_agg(t.translate(op.arg))
    if (where := op.where) is not None:
        arg = arg.filter(t.translate(where))
    return sa.func.array_join(arg, t.translate(op.sep))


def _array_index(t, op):
    return sa.func.element_at(t.translate(op.arg), t.translate(op.index) + 1)


def _array_column(t, op):
    args = ", ".join(
        str(t.translate(arg).compile(compile_kwargs={"literal_binds": True}))
        for arg in op.cols
    )
    return sa.literal_column(f"ARRAY[{args}]", type_=to_sqla_type(op.output_dtype))


def _day_of_week_index(t, op):
    sa_arg = t.translate(op.arg)

    return sa.cast(
        sa.cast(sa.func.day_of_week(sa_arg) + 6, sa.SMALLINT) % 7, sa.SMALLINT
    )


def _day_of_week_name(t, op):
    sa_arg = t.translate(op.arg)
    return sa.func.date_format(sa_arg, "%W")


def _capitalize(t, op):
    sa_arg = t.translate(op.arg)
    return sa.func.concat(
        sa.func.upper(sa.func.substring(sa_arg, 1, 2)), sa.func.substring(sa_arg, 2)
    )


def _string_right(t, op):
    sa_arg = t.translate(op.arg)
    sa_length = t.translate(op.nchars)

    return sa.func.substr(sa_arg, -sa_length)


_truncate_precisions = {
    's': 'second',
    'm': 'minute',
    'h': 'hour',
    'D': 'day',
    'W': 'week',
    'M': 'month',
    'Q': 'quarter',
    'Y': 'year',
}


def _timestamp_truncate(t, op):
    sa_arg = t.translate(op.arg)
    try:
        precision = _truncate_precisions[op.unit]
    except KeyError:
        raise com.UnsupportedOperationError(f'Unsupported truncate unit {op.unit!r}')
    return sa.func.date_trunc(precision, sa_arg)


def _date_from_ymd(t, op):
    ymdstr = sa.func.format(
        '%04d-%02d-%02d',
        t.translate(op.year),
        t.translate(op.month),
        t.translate(op.day),
    )
    return sa.func.from_iso8601_date(ymdstr)


operation_registry.update(
    {
        # conditional expressions
        # static checks are not happy with using "if" as a property
        ops.Where: fixed_arity(getattr(sa.func, 'if'), 3),
        # boolean reductions
        ops.Any: unary(sa.func.bool_or),
        ops.All: unary(sa.func.bool_and),
        ops.NotAny: unary(lambda x: sa.not_(sa.func.bool_or(x))),
        ops.NotAll: unary(lambda x: sa.not_(sa.func.bool_and(x))),
        ops.ArgMin: reduction(sa.func.min_by),
        ops.ArgMax: reduction(sa.func.max_by),
        # array ops
        ops.Correlation: _corr,
        ops.Covariance: _covar,
        ops.ExtractMillisecond: unary(sa.func.millisecond),
        ops.Arbitrary: _arbitrary,
        ops.ApproxCountDistinct: reduction(sa.func.approx_distinct),
        ops.GroupConcat: _group_concat,
        ops.BitAnd: reduction(sa.func.bitwise_and_agg),
        ops.BitOr: reduction(sa.func.bitwise_or_agg),
        ops.BitwiseAnd: fixed_arity(sa.func.bitwise_and, 2),
        ops.BitwiseOr: fixed_arity(sa.func.bitwise_or, 2),
        ops.BitwiseXor: fixed_arity(sa.func.bitwise_xor, 2),
        ops.BitwiseLeftShift: fixed_arity(sa.func.bitwise_left_shift, 2),
        ops.BitwiseRightShift: fixed_arity(sa.func.bitwise_right_shift, 2),
        ops.BitwiseNot: unary(sa.func.bitwise_not),
        ops.ArrayCollect: reduction(sa.func.array_agg),
        ops.ArrayConcat: fixed_arity(sa.func.concat, 2),
        ops.ArrayLength: unary(sa.func.cardinality),
        ops.ArrayIndex: _array_index,
        ops.ArrayColumn: _array_column,
        ops.JSONGetItem: _json_get_item,
        ops.ExtractDayOfYear: unary(sa.func.day_of_year),
        ops.ExtractWeekOfYear: unary(sa.func.week_of_year),
        ops.DayOfWeekIndex: _day_of_week_index,
        ops.DayOfWeekName: _day_of_week_name,
        ops.ExtractEpochSeconds: unary(sa.func.to_unixtime),
        ops.Translate: fixed_arity(sa.func.translate, 3),
        ops.Capitalize: _capitalize,
        ops.StrRight: _string_right,
        ops.StringSplit: fixed_arity(sa.func.split, 2),
        ops.Repeat: fixed_arity(
            lambda value, count: sa.func.array_join(sa.func.repeat(value, count), ''), 2
        ),
        ops.DateTruncate: _timestamp_truncate,
        ops.TimestampTruncate: _timestamp_truncate,
        ops.ArrayRepeat: fixed_arity(
            lambda arg, times: sa.func.flatten(sa.func.repeat(arg, times)), 2
        ),
        ops.DateFromYMD: _date_from_ymd,
    }
)
