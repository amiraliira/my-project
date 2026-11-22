-- Migration-account only; never execute DDL with runtime credentials.

CREATE OR REPLACE FUNCTION pak_round_even(p_value numeric,p_digits integer)
RETURNS numeric LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $$
DECLARE factor numeric; scaled numeric; whole numeric; fraction numeric;
BEGIN
 IF p_digits<0 OR p_digits>28 THEN RAISE EXCEPTION 'Unsupported decimal scale'; END IF;
 factor:=power(10::numeric,p_digits); scaled:=abs(p_value)*factor;
 whole:=trunc(scaled); fraction:=scaled-whole;
 IF fraction>0.5 OR (fraction=0.5 AND mod(whole,2)=1) THEN whole:=whole+1; END IF;
 RETURN sign(p_value)*whole::numeric(100,40)/factor;
END; $$;
CREATE OR REPLACE FUNCTION pak_decimal(p_value numeric)
RETURNS numeric LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $$
DECLARE places integer; rounded numeric;
BEGIN
 FOR places IN REVERSE 28..0 LOOP
  rounded:=pak_round_even(p_value,places);
  IF abs(rounded)*power(10::numeric,places)<=79228162514264337593543950335 THEN RETURN rounded; END IF;
 END LOOP;
 RAISE EXCEPTION 'Decimal score overflow';
END; $$;
CREATE OR REPLACE FUNCTION pak_participant_score(p_minutes integer,p_supervisor numeric,p_head numeric)
RETURNS numeric LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
 SELECT pak_decimal(pak_decimal(pak_decimal(p_minutes::numeric(80,50)/60)*p_supervisor)*p_head);
$$;
CREATE OR REPLACE FUNCTION pak_decimal_add(p_total numeric,p_value numeric)
RETURNS numeric LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
 SELECT pak_decimal(COALESCE(p_total,0)+COALESCE(p_value,0));
$$;
DROP AGGREGATE IF EXISTS pak_decimal_sum(numeric);
CREATE AGGREGATE pak_decimal_sum(numeric)(SFUNC=pak_decimal_add,STYPE=numeric,INITCOND='0',PARALLEL=SAFE);
